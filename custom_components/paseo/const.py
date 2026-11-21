"""Constants for the Paseo integration."""

from typing import Final

DOMAIN: Final = "paseo"

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"
CONF_WEBHOOK_ID: Final = "webhook_id"

# Contract version of the payload and REST API (the payload schema and REST API shared with the plugin).
SCHEMA_VERSION: Final = 2
# next_push_by_s values the schema allows (the payload schema).
ALLOWED_NEXT_PUSH_S: Final = frozenset({60, 300})
# The data is stale once no push arrived for this many times next_push_by_s.
STALE_FACTOR: Final = 2

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_AGENT_ID: Final = "agent_id"
SERVICE_RESUME_QUOTA_AGENT: Final = "resume_quota_agent"

ATTR_RUN_ID: Final = "run_id"
SERVICE_CANCEL_CI_RUN: Final = "cancel_ci_run"
