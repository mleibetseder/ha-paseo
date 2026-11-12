"""Constants for the Paseo integration."""

from typing import Final

DOMAIN: Final = "paseo"

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"
CONF_WEBHOOK_ID: Final = "webhook_id"

# Contract version of the payload and REST API (the payload schema and REST API shared with the plugin).
SCHEMA_VERSION: Final = 1
# next_push_by_s values the schema allows (the payload schema).
ALLOWED_NEXT_PUSH_S: Final = frozenset({60, 300})
# The data is stale once no push arrived for this many times next_push_by_s.
STALE_FACTOR: Final = 2

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_WORKTREE: Final = "worktree"
ATTR_QUESTION_ID: Final = "question_id"
ATTR_CHOICE: Final = "choice"
ATTR_WORDS: Final = "words"
ATTR_FINGERPRINT: Final = "fingerprint"
SERVICE_ANSWER_QUESTION: Final = "answer_question"
# Notification action `PASEO_ANSWER|<worktreeKey>|<questionId>|<letter>` (the mobile_app_notification_action event).
ACTION_PREFIX: Final = "PASEO_ANSWER"
ATTR_AGENT_ID: Final = "agent_id"
SERVICE_RESUME_QUOTA_AGENT: Final = "resume_quota_agent"

# Options.
CONF_NOTIFY_TARGETS: Final = "notify_targets"
CONF_BEDTIME_ENTITY: Final = "bedtime_entity"
CONF_REMINDER_MINUTES: Final = "reminder_minutes"
CONF_QUIET_HOLD: Final = "quiet_hold"
QUIET_HOLD_NON_BLOCKING: Final = "hold_non_blocking"
QUIET_HOLD_ALL: Final = "hold_all"
QUIET_HOLD_NEVER: Final = "never"
QUIET_HOLD_MODES: Final = (QUIET_HOLD_NON_BLOCKING, QUIET_HOLD_ALL, QUIET_HOLD_NEVER)
DEFAULT_BEDTIME_ENTITY: Final = "input_boolean.bedtime"
DEFAULT_REMINDER_MINUTES: Final = 15
NOTIFY_PREFIX: Final = "mobile_app_"
SERVICE_GET_MORNING_BRIEF: Final = "get_morning_brief"
ATTR_RUN_ID: Final = "run_id"
SERVICE_CANCEL_CI_RUN: Final = "cancel_ci_run"
