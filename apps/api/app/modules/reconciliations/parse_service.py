"""Service de parsing IA (S9 — BACK 7.1).

Responsabilidade: transformar bytes de upload em `ExtractedStatement`
chamando a Anthropic via `AnthropicClient`. Stateless — não persiste nada;
quem persiste é S10/BACK 8.1.

Fronteiras (CLAUDE.md §3.8 + §3.10):
    - Validação de tamanho, extensão e magic bytes acontece AQUI no servidor,
      mesmo que o front (S8) já tenha validado. Servidor é a fonte da
      verdade; front é UX.
    - Arquivo NUNCA toca disco. Bytes em memória, descartados ao final do
      request.

Pipeline para cada formato suportado:
    PDF   → bytes brutos   →   AnthropicClient (document base64).
    CSV   → decode utf-8   →   AnthropicClient (text block).
    XLSX  → openpyxl       →   render TSV   →   AnthropicClient (text block).
    XLS   → não suportado neste caminho (o xlrd lê o `.xls` só no leitor da origem
            por arquivo e do plano contábil, 86e3n70p6).

Arquivo grande é dividido em blocos extraídos em paralelo e juntados: o tempo
de uma chamada cresce com o número de linhas e estourava o teto de forma
determinística. Em TEXTO (CSV/XLSX) o corte é por linhas (`parse_chunking`,
86e39xvxm); em PDF é por PÁGINAS (`parse_pdf_pages`, 86e3ff8xd), com uma chamada
curta de identificação (banco e tipo de conta) só com a primeira página antes
dos blocos, porque página do meio não tem cabeçalho. A junção é a mesma.

Todo `/parse` termina com o evento `parse_completed` (tipo do arquivo, bytes,
se dividiu, blocos, páginas ou registros, movimentações, duração): só
contadores, nunca conteúdo, nome de arquivo ou descrição (§3.3, §4.5).
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import PurePosixPath

import openpyxl
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import AppError, ValidationAppError
from app.core.logging import get_logger
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.anthropic.schemas import (
    DocumentIdentity,
    ExtractedStatement,
    ExtractedTransaction,
)
from app.modules.reconciliations.parse_chunking import (
    correct_invoice_due_year,
    merge_statements,
    plan_blocks,
)
from app.modules.reconciliations.parse_pdf_pages import plan_pdf_blocks
from app.modules.reconciliations.processing.checksum import compute_checksum
from app.utils.magic_bytes import FileType, validate_upload_type

log = get_logger(__name__)

# Conjunto aceito pelo endpoint de parse. Mantém XLS de fora — o checklist da
# UI lista XLS, mas o parse por IA (BACK 7.1) não renderiza `.xls` para texto (o
# xlrd entrou nas deps em 86e3n70p6, só para o leitor determinístico da origem por
# arquivo). Se o cliente subir .xls, recusamos com mensagem clara.
_ALLOWED_FOR_PARSE: set[FileType] = {
    FileType.PDF,
    FileType.CSV,
    FileType.XLSX,
}

_DOCUMENT_KIND: dict[FileType, str] = {
    FileType.PDF: "extrato/fatura em PDF",
    FileType.CSV: "extrato/fatura em CSV",
    FileType.XLSX: "extrato/fatura em planilha XLSX",
}

_MIME_TYPE: dict[FileType, str] = {
    FileType.PDF: "application/pdf",
    FileType.CSV: "text/csv",
    # XLSX é convertido para texto antes de enviar — passamos `text/plain`
    # para o client renderizar como `text` block.
    FileType.XLSX: "text/plain",
}

_ALLOWED_EXTENSIONS = {".pdf", ".csv", ".xlsx", ".xls"}


class ParseService:
    """Orquestra a validação + chamada à IA."""

    def __init__(
        self,
        anthropic_client: AnthropicClient,
        *,
        mock_enabled: bool = False,
        mock_delay_seconds: float = 0.0,
        chunk_rows: int = 100,
        chunk_min_rows: int = 150,
        chunk_concurrency: int = 4,
        pdf_pages_per_block: int = 2,
        pdf_min_pages: int = 4,
        pdf_max_pages: int = 30,
    ) -> None:
        self._anthropic = anthropic_client
        # Extração em blocos (86e39xvxm). Fonte dos valores: `Settings`
        # (`ADL_PARSE_CHUNK_*`); os defaults aqui só servem a testes e scripts.
        # Um arquivo com menos linhas que um bloco inteiro não deve ser dividido
        # — o limiar nunca fica abaixo do tamanho do bloco.
        self._chunk_rows = chunk_rows
        self._chunk_min_rows = max(chunk_min_rows, chunk_rows)
        self._chunk_concurrency = chunk_concurrency
        # PDF por páginas (86e3ff8xd, `ADL_PARSE_PDF_*`): o mesmo semáforo acima
        # limita as chamadas simultâneas dos blocos de PDF.
        self._pdf_pages_per_block = pdf_pages_per_block
        self._pdf_min_pages = pdf_min_pages
        self._pdf_max_pages = pdf_max_pages
        # MOCK EXCLUSIVO DE DEMO — ver `Settings.MOCK_PARSE`. Quando ativo,
        # `parse_statement` ignora o `AnthropicClient` e devolve o payload
        # fictício da Padaria. As validações de tamanho/extensão/magic bytes
        # continuam rodando para que a UX de erro continue fiel.
        self._mock_enabled = mock_enabled
        self._mock_delay_seconds = mock_delay_seconds

    async def parse_statement(
        self,
        *,
        file_bytes: bytes,
        filename: str | None,
        max_upload_bytes: int,
    ) -> ExtractedStatement:
        """Valida o upload e chama a Anthropic para extrair movimentações.

        Args:
            file_bytes: bytes do upload (já lido em memória).
            filename: nome original do arquivo. Pode ser `None` (alguns
                clientes HTTP não setam). Usado apenas como heurística da
                extensão e para o `document_kind` do prompt — NUNCA para
                construir caminhos.
            max_upload_bytes: limite efetivo (`Settings.max_upload_bytes`).

        Returns:
            `ExtractedStatement` validado.

        Raises:
            ValidationAppError: arquivo vazio, > limite, extensão proibida,
                magic bytes não reconhecidos, `.xls` (não suportado nesta
                versão), ou PDF com mais páginas que `pdf_max_pages`
                (`PdfTooManyPagesError`, com orientação para dividir).
            AnthropicAuthError / AnthropicTimeoutError / AnthropicParseError:
                propagadas do `AnthropicClient` para o handler global.
        """
        self._validate_size(file_bytes, max_upload_bytes)
        self._validate_extension(filename)

        # Magic bytes valida o tipo REAL (rejeita .pdf falsificado contendo XLSX).
        # `_ALLOWED_FOR_PARSE` exclui XLS — mensagem específica abaixo cobre o
        # caso de XLS detectado nos magic bytes.
        from app.utils.magic_bytes import detect_file_type

        detected_raw = detect_file_type(file_bytes)
        if detected_raw == FileType.XLS:
            raise ValidationAppError(
                "Arquivo .xls não é suportado por enquanto.",
                user_message=(
                    "Arquivos .xls (Excel 97-2003) não são suportados. "
                    "Salve como .xlsx ou .csv e tente novamente."
                ),
            )
        detected = validate_upload_type(file_bytes, allowed=_ALLOWED_FOR_PARSE)

        started = time.monotonic()
        content, mime_type = self._prepare_content(detected, file_bytes)
        document_kind = _DOCUMENT_KIND[detected]
        file_type = detected.value
        bytes_in = len(file_bytes)

        if self._mock_enabled:
            log.warning(
                "parse_mock_used",
                bytes_in=bytes_in,
                detected=file_type,
                delay_s=self._mock_delay_seconds,
            )
            if self._mock_delay_seconds > 0:
                await asyncio.sleep(self._mock_delay_seconds)
            statement = _MOCK_PADARIA_STATEMENT.model_copy(deep=True)
            self._log_completed(
                file_type=file_type,
                bytes_in=bytes_in,
                blocks=1,
                statement=statement,
                started=started,
            )
            return statement

        # Tamanho do arquivo na unidade da divisão (páginas ou registros), para
        # o `parse_completed` do caminho inteiro.
        size_fields: dict[str, int] = {}

        if detected == FileType.PDF:
            # `pypdf` é síncrono e lê arquivo de terceiro: fora do event loop.
            pdf_plan = await run_in_threadpool(
                plan_pdf_blocks,
                file_bytes,
                pages_per_block=self._pdf_pages_per_block,
                min_pages=self._pdf_min_pages,
                max_pages=self._pdf_max_pages,
            )
            if pdf_plan.skipped_reason is not None:
                log.info(
                    "parse_pdf_split_skipped",
                    reason=pdf_plan.skipped_reason,
                    pages=pdf_plan.pages,
                    bytes_in=bytes_in,
                )
            if pdf_plan.is_split and pdf_plan.first_page is not None:
                # D2: identificação curta pela primeira página ANTES dos blocos
                # (página do meio não tem cabeçalho); depois todos os blocos,
                # inclusive o primeiro, em paralelo.
                identity = await self._anthropic.identify_document(pdf_plan.first_page)
                statement = await self._extract_in_blocks(
                    pdf_plan.blocks,
                    mime_type=mime_type,
                    document_kind=document_kind,
                    file_type=file_type,
                    bytes_in=bytes_in,
                    identity=identity,
                    pages=pdf_plan.pages,
                )
                self._log_completed(
                    file_type=file_type,
                    bytes_in=bytes_in,
                    blocks=len(pdf_plan.blocks),
                    statement=statement,
                    started=started,
                    pages=pdf_plan.pages,
                )
                return statement
            size_fields = {"pages": pdf_plan.pages}

        elif detected in (FileType.CSV, FileType.XLSX):
            text_plan = plan_blocks(
                AnthropicClient._decode_text(content),
                chunk_rows=self._chunk_rows,
                min_rows=self._chunk_min_rows,
            )
            if text_plan.is_split:
                statement = await self._extract_in_blocks(
                    [block.encode("utf-8") for block in text_plan.blocks],
                    mime_type=mime_type,
                    document_kind=document_kind,
                    file_type=file_type,
                    bytes_in=bytes_in,
                    rows=text_plan.data_records,
                )
                self._log_completed(
                    file_type=file_type,
                    bytes_in=bytes_in,
                    blocks=len(text_plan.blocks),
                    statement=statement,
                    started=started,
                    data_records=text_plan.data_records,
                )
                return statement
            size_fields = {"data_records": text_plan.data_records}

        # Arquivo inteiro: a junção não roda, mas o ano inventado do vencimento
        # (86e3n70qf) aparece igual — a mesma correção da junção vale aqui.
        statement = correct_invoice_due_year(
            await self._anthropic.extract_movements(
                content=content,
                mime_type=mime_type,
                document_kind=document_kind,
            )
        )
        self._log_completed(
            file_type=file_type,
            bytes_in=bytes_in,
            blocks=1,
            statement=statement,
            started=started,
            **size_fields,
        )
        return statement

    @staticmethod
    def _log_completed(
        *,
        file_type: str,
        bytes_in: int,
        blocks: int,
        statement: ExtractedStatement,
        started: float,
        **size_fields: int,
    ) -> None:
        """Eventos `parse_completed` (D7) e `parse_checksum`, um de cada por `/parse`.

        `parse_completed` leva só contadores. `parse_checksum` (86e3n70qf) leva o
        resultado da identidade de saldos que a rota devolve ao front, para que
        um "os saldos não fecham" seja lido no log de dev sem depender de print:
        só números e o tipo de conta, nunca descrição (§3.3, §4.5). Valor em
        claro não é PII (§4.3) e sai como texto com 2 casas, nunca `float`.
        """
        log.info(
            "parse_completed",
            file_type=file_type,
            bytes_in=bytes_in,
            split=blocks > 1,
            blocks=blocks,
            transaction_count=len(statement.transactions),
            duration_ms=round((time.monotonic() - started) * 1000),
            **size_fields,
        )
        checksum = compute_checksum(statement)
        log.info(
            "parse_checksum",
            applicable=checksum.applicable,
            ok=checksum.ok,
            expected=f"{checksum.expected:.2f}",
            computed=f"{checksum.computed:.2f}",
            difference=f"{checksum.difference:.2f}",
            blocks=blocks,
            account_type=checksum.account_type,
        )

    async def _extract_in_blocks(
        self,
        blocks: list[bytes],
        *,
        mime_type: str,
        document_kind: str,
        file_type: str,
        bytes_in: int,
        identity: DocumentIdentity | None = None,
        **size_fields: int,
    ) -> ExtractedStatement:
        """Uma chamada por bloco, em paralelo limitado, e a junção na ordem.

        `blocks` são os bytes de cada chamada: PDF de páginas (`parse_pdf_pages`)
        ou texto já codificado (`parse_chunking`); `identity` é a identificação
        do PDF dividido, repassada a todo bloco e à junção (D2, D5).
        Semáforo limita as chamadas simultâneas (rate limit da conta Anthropic).
        `TaskGroup`: a primeira falha cancela os blocos ainda pendentes — não se
        gasta crédito extraindo o resto de um arquivo que já não vai fechar — e
        o erro tipado do bloco (timeout, parse, auth) sobe como se fosse do
        arquivo inteiro. O sinal `failed` fecha a janela entre a falha e o
        cancelamento: o bloco que acorda no semáforo nesse instante NÃO chama a
        API (sem ele, uma requisição a mais já teria saído). Nada de conteúdo em
        log: só contadores (§3.3, §4.5).
        """
        started = time.monotonic()
        total = len(blocks)
        semaphore = asyncio.Semaphore(self._chunk_concurrency)
        failed = asyncio.Event()

        async def _extract_one(index: int, block: bytes) -> ExtractedStatement:
            async with semaphore:
                if failed.is_set():
                    raise asyncio.CancelledError
                try:
                    return await self._anthropic.extract_movements(
                        content=block,
                        mime_type=mime_type,
                        document_kind=document_kind,
                        part=(index + 1, total),
                        identity=identity,
                    )
                except BaseException:
                    failed.set()
                    raise

        try:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(_extract_one(i, block)) for i, block in enumerate(blocks)
                ]
        except ExceptionGroup as failures:
            # O TaskGroup embrulha; devolvemos o PRIMEIRO erro de domínio como o
            # caller já espera (o handler global conhece `AppError`, não grupos).
            first = next((exc for exc in failures.exceptions if isinstance(exc, AppError)), None)
            if first is None:
                raise
            raise first from None

        statement = merge_statements([task.result() for task in tasks], identity=identity)
        log.info(
            "parse_chunked",
            file_type=file_type,
            blocks=total,
            concurrency=self._chunk_concurrency,
            bytes_in=bytes_in,
            transaction_count=len(statement.transactions),
            duration_ms=round((time.monotonic() - started) * 1000),
            **size_fields,
        )
        return statement

    # ------------------------------------------------------------------
    # Validações
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_size(file_bytes: bytes, max_upload_bytes: int) -> None:
        size = len(file_bytes)
        if size == 0:
            raise ValidationAppError(
                "Upload vazio.",
                user_message="O arquivo enviado está vazio.",
            )
        if size > max_upload_bytes:
            raise ValidationAppError(
                f"Upload excede {max_upload_bytes} bytes ({size} recebidos).",
                user_message=(
                    f"O arquivo excede o limite de {max_upload_bytes // (1024 * 1024)} MB."
                ),
            )

    @staticmethod
    def _validate_extension(filename: str | None) -> None:
        """Filtro pela extensão. Magic bytes faz a checagem real depois.

        Aceitar `filename=None` é proposital — alguns clientes HTTP omitem
        o filename e queremos cair no magic bytes em seguida. Quando
        presente, sanitizamos via `PurePosixPath` para evitar path
        traversal em logs (`..\\..\\evil.pdf` vira só `evil.pdf`).
        """
        if filename is None:
            return
        # Sanitização: pega só o nome final, ignora qualquer separador.
        # `PurePosixPath` é seguro mesmo com input vindo do Windows (`\` é
        # tratado como caractere comum, mas o front normaliza; a segunda
        # linha cobre o resto).
        clean = PurePosixPath(filename).name
        clean = clean.replace("\\", "/").rsplit("/", 1)[-1]
        ext = ("." + clean.rsplit(".", 1)[-1].lower()) if "." in clean else ""
        if ext not in _ALLOWED_EXTENSIONS:
            raise ValidationAppError(
                f"Extensão {ext or '(sem extensão)'} não permitida.",
                user_message="Formato não suportado. Envie PDF, CSV, XLS ou XLSX.",
            )

    # ------------------------------------------------------------------
    # Conversão para o formato esperado pela IA
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_content(file_type: FileType, file_bytes: bytes) -> tuple[bytes, str]:
        """Converte bytes brutos no formato consumido pelo `AnthropicClient`.

        Returns:
            Tupla `(content_bytes, mime_type)`. Para PDF, o `content_bytes`
            é o PDF original — vira `document` block base64. Para outros
            formatos, é o texto utf-8 já extraído — vira `text` block.
        """
        if file_type == FileType.PDF:
            return file_bytes, _MIME_TYPE[FileType.PDF]

        if file_type == FileType.CSV:
            # CSV vai cru (decodificação fica com o client). Magic bytes já
            # garantiu que é texto válido (utf-8 ou latin-1).
            return file_bytes, _MIME_TYPE[FileType.CSV]

        if file_type == FileType.XLSX:
            text = _xlsx_to_text(file_bytes)
            return text.encode("utf-8"), _MIME_TYPE[FileType.XLSX]

        # Qualquer outro tipo deveria ter sido rejeitado por
        # `validate_upload_type` antes — defensivo.
        raise ValidationAppError(
            f"Tipo {file_type} não suportado pelo parsing.",
            user_message="Formato não suportado nesta versão.",
        )


def _xlsx_to_text(file_bytes: bytes) -> str:
    """Renderiza um XLSX como texto tabular (TSV) para a IA.

    Princípio: enviar texto plano é dramaticamente mais barato em tokens do
    que mandar o XLSX cru via document block (que o Claude nem aceita
    nativamente fora de PDFs/imagens). A IA recebe linhas separadas por
    `\\n`, células separadas por `\\t` — formato que o modelo entende
    facilmente.

    Raises:
        ValidationAppError: arquivo XLSX corrompido ou criptografado
            (openpyxl explode com `BadZipFile` / `InvalidFileException`).
    """
    # NÃO usar `read_only=True`: nesse modo o openpyxl confia na tag
    # `<dimension>` do XML e itera só o range declarado. Extratos exportados
    # por banco (ex.: Banco Inter / DM Construções, jun/2026) vêm com
    # `<dimension>` errada/menor que os dados → `iter_rows` lia só as primeiras
    # linhas e a IA extraía 1 de ~20 lançamentos (Report #4). O modo normal lê
    # todas as células de fato. Custo de memória é aceitável (upload ≤ 20 MB).
    try:
        wb = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise ValidationAppError(
            f"XLSX inválido ou corrompido: {exc}",
            user_message=(
                "Não foi possível ler o arquivo XLSX. Verifique se está íntegro e "
                "sem proteção por senha."
            ),
        ) from exc

    parts: list[str] = []
    for ws in wb.worksheets:
        if ws.max_row == 0 or ws.max_column == 0:
            continue
        parts.append(f"# Aba: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            # Pula linhas totalmente vazias (todas as células vazias após strip).
            if not any(c.strip() for c in cells):
                continue
            parts.append("\t".join(cells))
    wb.close()

    if not parts:
        raise ValidationAppError(
            "XLSX sem dados.",
            user_message="O arquivo XLSX não contém dados legíveis.",
        )
    return "\n".join(parts)


# ----------------------------------------------------------------------
# Payload do mock (Settings.MOCK_PARSE) — ver __init__ do ParseService.
# Espelha 1:1 o PDF gerado por `d:\tmp\gen_extrato_demo.py` (Padaria Pão
# Quente, abril/2026). Não tem nada de produção; só serve para gravar
# vídeo quando a conta da Anthropic está sem crédito.
# ----------------------------------------------------------------------

_MOCK_TRANSACTIONS: list[ExtractedTransaction] = [
    ExtractedTransaction(
        date=date(2026, 4, 2),
        description="PIX RECEBIDO LUIZA RAMOS",
        amount=Decimal("32.00"),
        balance=Decimal("18532.00"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 2),
        description="TARIFA BANCARIA PACOTE PJ",
        amount=Decimal("-29.90"),
        balance=Decimal("18502.10"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 3),
        description="FORNECEDOR MOINHO PRADO LTDA",
        amount=Decimal("-1250.00"),
        balance=Decimal("17252.10"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 3),
        description="PIX RECEBIDO JOAO PEREIRA",
        amount=Decimal("78.50"),
        balance=Decimal("17330.60"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 4),
        description="DEPOSITO CIELO LIQUIDACAO",
        amount=Decimal("1245.30"),
        balance=Decimal("18575.90"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 4),
        description="PIX RECEBIDO MARIA SOUZA",
        amount=Decimal("65.00"),
        balance=Decimal("18640.90"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 5),
        description="DISTRIBUIDORA LACTOS REI",
        amount=Decimal("-567.80"),
        balance=Decimal("18073.10"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 7),
        description="DEPOSITO STONE LIQUIDACAO",
        amount=Decimal("893.75"),
        balance=Decimal("18966.85"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 7),
        description="FERMENTO BIOLOGICO IND SA",
        amount=Decimal("-234.50"),
        balance=Decimal("18732.35"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 8),
        description="PIX RECEBIDO BRUNO LIMA",
        amount=Decimal("48.00"),
        balance=Decimal("18780.35"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 9),
        description="ENEL DISTRIBUIDORA SP",
        amount=Decimal("-487.90"),
        balance=Decimal("18292.45"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 10),
        description="DEPOSITO CIELO LIQUIDACAO",
        amount=Decimal("1567.40"),
        balance=Decimal("19859.85"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 10),
        description="TRANSF CLIENTE FILIAL B",
        amount=Decimal("2000.00"),
        balance=Decimal("21859.85"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 11),
        description="ALUGUEL IMOVEL ABRIL 2026",
        amount=Decimal("-4500.00"),
        balance=Decimal("17359.85"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 14),
        description="PIX RECEBIDO ANA COSTA",
        amount=Decimal("28.50"),
        balance=Decimal("17388.35"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 14),
        description="DEPOSITO STONE LIQUIDACAO",
        amount=Decimal("742.10"),
        balance=Decimal("18130.45"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 15),
        description="CLARO COMUNICACOES PJ",
        amount=Decimal("-189.00"),
        balance=Decimal("17941.45"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 16),
        description="FOLHA PAGAMENTO 1A QUINZENA",
        amount=Decimal("-6225.00"),
        balance=Decimal("11716.45"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 17),
        description="DEPOSITO CIELO LIQUIDACAO",
        amount=Decimal("1389.20"),
        balance=Decimal("13105.65"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 17),
        description="FORN FARINHA OURO BRANCO",
        amount=Decimal("-980.00"),
        balance=Decimal("12125.65"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 20),
        description="PIX RECEBIDO RICARDO ALMEIDA",
        amount=Decimal("85.00"),
        balance=Decimal("12210.65"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 21),
        description="INSS COMP 04/2026",
        amount=Decimal("-1890.00"),
        balance=Decimal("10320.65"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 22),
        description="DEPOSITO CIELO LIQUIDACAO",
        amount=Decimal("1678.50"),
        balance=Decimal("11999.15"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 23),
        description="COMBUSTIVEL POSTO IPIRANGA",
        amount=Decimal("-178.00"),
        balance=Decimal("11821.15"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 24),
        description="DEPOSITO STONE LIQUIDACAO",
        amount=Decimal("932.40"),
        balance=Decimal("12753.55"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 24),
        description="PIX RECEBIDO PEDRO HENRIQUE",
        amount=Decimal("42.00"),
        balance=Decimal("12795.55"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 27),
        description="FOLHA PAGAMENTO 2A QUINZENA",
        amount=Decimal("-6225.00"),
        balance=Decimal("6570.55"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 28),
        description="FORN EMBALAGENS BR LTDA",
        amount=Decimal("-345.60"),
        balance=Decimal("6224.95"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 29),
        description="DEPOSITO CIELO LIQUIDACAO",
        amount=Decimal("1456.80"),
        balance=Decimal("7681.75"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 30),
        description="TARIFA TED PJ",
        amount=Decimal("-12.30"),
        balance=Decimal("7669.45"),
    ),
    ExtractedTransaction(
        date=date(2026, 4, 30),
        description="IOF MENSAL",
        amount=Decimal("-8.50"),
        balance=Decimal("7660.95"),
    ),
]

_MOCK_PADARIA_STATEMENT = ExtractedStatement(
    bank_name="Banco Itaú Unibanco S.A.",
    account_type="checking",
    period_start=date(2026, 4, 1),
    period_end=date(2026, 4, 30),
    opening_balance=Decimal("18500.00"),
    closing_balance=Decimal("7660.95"),
    transactions=_MOCK_TRANSACTIONS,
)
