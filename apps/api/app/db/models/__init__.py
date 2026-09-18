"""Modelos SQLAlchemy do projeto.

Importar Base e os modelos a partir daqui garante que o Alembic detecta
todos para autogenerate (`alembic revision --autogenerate`).
"""

from app.db.base import Base
from app.db.models.access_audit import AccessAudit
from app.db.models.anomaly_type import AnomalySeverity, AnomalyType
from app.db.models.client import IV_HEX_LENGTH, Client
from app.db.models.client_assignment import (
    UQ_CLIENT_ASSIGNMENT_CLIENT_USER,
    UQ_CLIENT_ASSIGNMENT_PRIMARY,
    ClientAssignment,
    primary_assignment_index_predicate,
)
from app.db.models.client_category import (
    MAX_CATEGORY_NAME_CHARS,
    UQ_CLIENT_CATEGORY_ORGANIZATION_NAME,
    ClientCategory,
    ClientCategoryTone,
)
from app.db.models.client_glossary_entry import (
    MAX_CODE_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_ENTRIES_PER_CLIENT,
    MAX_NAME_CHARS,
    ClientGlossaryEntry,
    GlossaryEntryKind,
)
from app.db.models.notification import Notification, NotificationType
from app.db.models.omie_account_cache import OmieAccountCache, OmieAccountType
from app.db.models.organization import (
    HOLOGRAM_ORGANIZATION_ID,
    HOLOGRAM_ORGANIZATION_NAME,
    MAX_ORGANIZATION_NAME_CHARS,
    UQ_ORGANIZATION_NAME,
    Organization,
    organization_id_server_default,
)
from app.db.models.reconciliation_anomaly import (
    AnomalyDetectedBy,
    AnomalyReviewVerdict,
    ReconciliationAnomaly,
)
from app.db.models.reconciliation_file import ReconciliationFile, ReconciliationFileStatus
from app.db.models.reconciliation_file_entry import (
    FileEntrySituation,
    FileEntryUserAction,
    ReconciliationFileEntry,
)
from app.db.models.reconciliation_omie_entry import (
    OmieEntryStatus,
    OmieEntryUserAction,
    ReconciliationOmieEntry,
)
from app.db.models.reconciliation_omie_posting import (
    COD_INT_LANC_MAX_LENGTH,
    OmiePostingStatus,
    ReconciliationOmiePosting,
)
from app.db.models.reconciliation_session import (
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
)
from app.db.models.usage_event import UsageEvent
from app.db.models.user import (
    CLIENT_ROLES,
    PLATFORM_ROLES,
    SCOPE_CONSISTENCY_CHECK,
    SCOPE_CONSISTENCY_CONSTRAINT,
    SYSTEM_ROLES,
    ClientUserRole,
    SystemUserRole,
    User,
    UserRole,
    UserScope,
)
from app.db.models.user_client_favorite import UQ_USER_CLIENT_FAVORITE, UserClientFavorite

__all__ = [
    "CLIENT_ROLES",
    "COD_INT_LANC_MAX_LENGTH",
    "HOLOGRAM_ORGANIZATION_ID",
    "HOLOGRAM_ORGANIZATION_NAME",
    "IV_HEX_LENGTH",
    "MAX_CATEGORY_NAME_CHARS",
    "MAX_CODE_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_ENTRIES_PER_CLIENT",
    "MAX_NAME_CHARS",
    "MAX_ORGANIZATION_NAME_CHARS",
    "PLATFORM_ROLES",
    "SCOPE_CONSISTENCY_CHECK",
    "SCOPE_CONSISTENCY_CONSTRAINT",
    "SYSTEM_ROLES",
    "UQ_CLIENT_ASSIGNMENT_CLIENT_USER",
    "UQ_CLIENT_ASSIGNMENT_PRIMARY",
    "UQ_CLIENT_CATEGORY_ORGANIZATION_NAME",
    "UQ_ORGANIZATION_NAME",
    "UQ_USER_CLIENT_FAVORITE",
    "AccessAudit",
    "AnomalyDetectedBy",
    "AnomalyReviewVerdict",
    "AnomalySeverity",
    "AnomalyType",
    "Base",
    "Client",
    "ClientAssignment",
    "ClientCategory",
    "ClientCategoryTone",
    "ClientGlossaryEntry",
    "ClientUserRole",
    "FileEntrySituation",
    "FileEntryUserAction",
    "GlossaryEntryKind",
    "Notification",
    "NotificationType",
    "OmieAccountCache",
    "OmieAccountType",
    "OmieEntryStatus",
    "OmieEntryUserAction",
    "OmiePostingStatus",
    "Organization",
    "ReconciliationAnomaly",
    "ReconciliationFile",
    "ReconciliationFileEntry",
    "ReconciliationFileStatus",
    "ReconciliationOmieEntry",
    "ReconciliationOmiePosting",
    "ReconciliationSession",
    "ReconciliationStatus",
    "SessionAccountType",
    "SystemUserRole",
    "UsageEvent",
    "User",
    "UserClientFavorite",
    "UserRole",
    "UserScope",
    "organization_id_server_default",
    "primary_assignment_index_predicate",
]
