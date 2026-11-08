from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cir_database_url: str = "postgresql+psycopg://cir_app:cir_app_local@127.0.0.1:55438/cir"
    cir_migration_database_url: str = "postgresql+psycopg://cir_owner:cir_owner_local@127.0.0.1:55438/cir"
    cir_crm_base_url: str = "http://127.0.0.1:28611"
    cir_support_base_url: str = "http://127.0.0.1:28621"
    crm_read_token: str = "crm-read-local"
    support_token: str = "support-local"
    crm_admin_token: str = "crm-admin-local"
    support_admin_token: str = "support-admin-local"
    cir_webhook_secret: str = "webhook-local-dev"
    cir_cookie_secure: bool = False
    cir_environment: str = "local"
    cir_session_hours: int = 12
    cir_cors_origins: str = "http://127.0.0.1:8100,http://localhost:8100"
    cir_investigator_mode: str = "deterministic"
    cir_llm_base_url: str = ""
    cir_llm_api_key: str = ""
    cir_llm_model: str = ""
    cir_llm_timeout_seconds: float = 30
    hubspot_token: str = ""
    hubspot_contact_id: str = ""
    hubspot_plan_property: str = ""
    hubspot_base_url: str = "https://api.hubapi.com"
    cir_trace_path: str = "var/traces.jsonl"
    cir_log_dir: str = "var/logs"
    otel_exporter_otlp_endpoint: str = ""
    cir_max_tool_calls: int = 8
    cir_max_tokens: int = 8000
    cir_max_elapsed_seconds: float = 45
    cir_max_estimated_cost_usd: float = 0.05
    # strict rejects a missing price. local_unmeasured keeps token, tool, response, and time limits
    # and labels money as unmeasured. estimated uses the two rates below and labels the result an estimate.
    cir_cost_policy: str = "strict"
    cir_input_usd_per_million: float = 0
    cir_output_usd_per_million: float = 0
    cir_lease_seconds: int = 30
    cir_repair_max_attempts: int = 5
    cir_proposal_ttl_minutes: int = 30

    def validate_runtime(self) -> None:
        if self.cir_environment not in {"local", "test", "production"}:
            raise ValueError("CIR_ENVIRONMENT must be local, test or production")
        if self.cir_environment != "production":
            return
        secrets = (self.crm_read_token, self.support_token, self.crm_admin_token, self.support_admin_token, self.cir_webhook_secret)
        if not self.cir_cookie_secure or any(len(value) < 32 or "local" in value.lower() for value in secrets) or len(set(secrets)) != len(secrets):
            raise ValueError("Production requires secure cookies and separate injected secrets of at least 32 characters")
        if not self.cors_origins or any(not origin.startswith("https://") for origin in self.cors_origins):
            raise ValueError("Production requires explicit HTTPS CORS origins")
        if "local" in self.cir_database_url or "@127.0.0.1" in self.cir_database_url:
            raise ValueError("Production requires an explicitly configured private database URL")
        if self.cir_investigator_mode != "deterministic":
            raise ValueError("This cloud release is scoped to deterministic synthetic mode")

    def cost_controls(self) -> dict:
        policy = (self.cir_cost_policy or "strict").strip().lower()
        if policy not in {"strict", "local_unmeasured", "estimated"}:
            policy = "strict"
        return {
            "max_estimated_cost_usd": self.cir_max_estimated_cost_usd,
            "cost_policy": policy,
            "input_usd_per_million": self.cir_input_usd_per_million,
            "output_usd_per_million": self.cir_output_usd_per_million,
        }

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cir_cors_origins.split(",") if item.strip()]

    @property
    def psycopg_url(self) -> str:
        return self.cir_database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def migration_psycopg_url(self) -> str:
        return self.cir_migration_database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
