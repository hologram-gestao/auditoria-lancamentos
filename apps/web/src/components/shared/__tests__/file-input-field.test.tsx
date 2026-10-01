/**
 * Campo de arquivo compartilhado (86e3gkd4y): o gatilho é um BOTÃO de verdade
 * (cursor, hover e anel de foco do `buttonVariants`), não o input de arquivo
 * cru, cujo botão nativo não muda o cursor nem reage ao hover.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { FileInputField, formatFileSize } from '@/components/shared/file-input-field';
import { assertNoA11yViolations } from '@/test/a11y';

function csv(name = 'plano.csv', content = 'codigo;nome\n1;Caixa') {
  return new File([content], name, { type: 'text/csv' });
}

function Controlled({
  initial = null,
  onChange,
  disabled,
  invalid,
}: {
  initial?: File | null;
  onChange?: (file: File | null) => void;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const [value, setValue] = useState<File | null>(initial);
  return (
    <>
      <label htmlFor="campo">Planilha (.csv)</label>
      <FileInputField
        id="campo"
        accept=".csv"
        value={value}
        disabled={disabled}
        aria-invalid={invalid}
        onChange={(file) => {
          setValue(file);
          onChange?.(file);
        }}
      />
      <button type="button" onClick={() => setValue(null)}>
        Limpar por fora
      </button>
    </>
  );
}

describe('FileInputField', () => {
  it('o gatilho tem cara de botão: cursor, hover e anel de foco vindo do input', () => {
    render(<Controlled />);
    const gatilho = screen.getByText('Escolher arquivo').closest('label');
    expect(gatilho).not.toBeNull();
    expect(gatilho).toHaveClass('cursor-pointer');
    expect(gatilho).toHaveClass('hover:bg-accent');
    expect(gatilho).toHaveClass('peer-focus-visible:ring-2');
    const input = screen.getByLabelText(/Planilha \(\.csv\)/, { selector: 'input' });
    expect(input).toHaveAttribute('type', 'file');
    expect(input).toHaveClass('peer', 'sr-only');
    expect(input).toHaveAttribute('accept', '.csv');
  });

  it('escolher mostra nome e tamanho, sem tooltip nativo; Remover volta ao gatilho', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText(/Planilha/, { selector: 'input' });

    const file = csv();
    await user.upload(input, file);
    expect(onChange).toHaveBeenLastCalledWith(file);
    const nome = screen.getByText('plano.csv');
    expect(nome).not.toHaveAttribute('title');
    expect(screen.getByText(formatFileSize(file.size))).toBeInTheDocument();
    expect(screen.queryByText('Escolher arquivo')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Remover arquivo selecionado' }));
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(screen.getByText('Escolher arquivo')).toBeInTheDocument();
  });

  it('valor zerado por fora limpa o input nativo: o MESMO arquivo dispara de novo', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText<HTMLInputElement>(/Planilha/, { selector: 'input' });
    const file = csv();

    await user.upload(input, file);
    await user.click(screen.getByRole('button', { name: 'Limpar por fora' }));
    expect(input.value).toBe('');
    await user.upload(input, file);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(screen.getByText('plano.csv')).toBeInTheDocument();
  });

  it('o ref encaminhado é o input real (o RHF foca o campo com erro por ele)', () => {
    const ref = createRef<HTMLInputElement>();
    render(<FileInputField ref={ref} accept=".csv" value={null} onChange={() => {}} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
    expect(ref.current?.type).toBe('file');
  });

  it('inválido pinta a borda destructive no gatilho; desabilitado apaga e trava', () => {
    const { unmount } = render(<Controlled invalid />);
    expect(screen.getByText('Escolher arquivo').closest('label')).toHaveClass('border-destructive');
    unmount();
    render(<Controlled disabled />);
    const gatilho = screen.getByText('Escolher arquivo').closest('label');
    expect(gatilho).toHaveAttribute('aria-disabled', 'true');
    expect(gatilho).toHaveClass('pointer-events-none', 'opacity-50');
    expect(screen.getByLabelText(/Planilha/, { selector: 'input' })).toBeDisabled();
  });

  it('aceita rótulo próprio no gatilho', () => {
    render(
      <FileInputField accept=".csv" value={null} onChange={() => {}} label="Trocar planilha" />,
    );
    expect(screen.getByText('Trocar planilha')).toBeInTheDocument();
  });

  it('sem violações de a11y nos dois estados', async () => {
    const { container } = render(<Controlled />);
    await assertNoA11yViolations(container);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText(/Planilha/, { selector: 'input' }), csv());
    await assertNoA11yViolations(container);
  });
});
