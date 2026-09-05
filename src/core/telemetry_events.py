# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Agent event names vendored from the website analytics registry."""
from __future__ import annotations

REGISTRY_VERSION = 43

PLUGIN_FIRST_OPEN = "plugin_first_open"
PLUGIN_OPENED = "plugin_opened"
PLUGIN_ACTIVATED = "plugin_activated"
TELEMETRY_OPT_CHANGED = "telemetry_opt_changed"
AGENT_RUN_STARTED = "agent_run_started"
AGENT_RUN_ENDED = "agent_run_ended"
AGENT_RUN_COMPLETED = "agent_run_completed"
AGENT_RUN_FAILED = "agent_run_failed"
AGENT_TOOL_CALLED = "agent_tool_called"
AGENT_PERMISSION_ASKED = "agent_permission_asked"
AGENT_PERMISSION_DECIDED = "agent_permission_decided"
AGENT_QUESTION_ASKED = "agent_question_asked"
AGENT_QUESTION_ANSWERED = "agent_question_answered"
AGENT_QUESTION_AUTO_ANSWERED = "agent_question_auto_answered"
AGENT_UNDO_USED = "agent_undo_used"
PLUGIN_ERROR = "plugin_error"
QUOTA_EXHAUSTED = "quota_exhausted"
LOW_BALANCE_CARD_SHOWN = "low_balance_card_shown"
FIRST_RUN_MILESTONE = "first_run_milestone"
PERMISSION_LEVEL_CHANGED = "permission_level_changed"
EFFORT_CHANGED = "effort_changed"
THREAD_CREATED = "thread_created"
THREAD_DELETED = "thread_deleted"
ATTACHMENT_ADDED = "attachment_added"
ERROR_REPORT_OPENED = "error_report_opened"
CONTACT_OPENED = "contact_opened"
TUTORIAL_OPENED = "tutorial_opened"
REVIEW_OPENED = "review_opened"
ACCOUNT_SIGNED_OUT = "account_signed_out"
WELCOME_DISMISSED = "welcome_dismissed"
SUBSCRIBE_LINK_CLICKED = "subscribe_link_clicked"
PLUGIN_UPDATE_PROMPT_SHOWN = "plugin_update_prompt_shown"
PLUGIN_UPDATE_PROMPT_CLICKED = "plugin_update_prompt_clicked"
PLUGIN_UPDATE_PROMPT_SUPPRESSED = "plugin_update_prompt_suppressed"
EXAMPLE_CHOSEN = "example_chosen"
PAIRING_STARTED = "pairing_started"
PAIRING_FAILED = "pairing_failed"
CONNECTION_FAILED = "connection_failed"
PLUGIN_UPDATED = "plugin_updated"
PLUGIN_UNLOADED = "plugin_unloaded"
LIBRARY_OPENED = "library_opened"

DEMO_DATA_LOADED = "demo_data_loaded"

ALL_EVENTS = frozenset({
    PLUGIN_FIRST_OPEN, PLUGIN_OPENED, PLUGIN_ACTIVATED, TELEMETRY_OPT_CHANGED,
    AGENT_RUN_STARTED, AGENT_RUN_ENDED, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED,
    AGENT_TOOL_CALLED, AGENT_PERMISSION_ASKED, AGENT_PERMISSION_DECIDED,
    AGENT_QUESTION_ASKED, AGENT_QUESTION_ANSWERED, AGENT_QUESTION_AUTO_ANSWERED,
    AGENT_UNDO_USED, PLUGIN_ERROR,
    QUOTA_EXHAUSTED, LOW_BALANCE_CARD_SHOWN, FIRST_RUN_MILESTONE,
    PERMISSION_LEVEL_CHANGED, EFFORT_CHANGED, THREAD_CREATED, THREAD_DELETED, ATTACHMENT_ADDED,
    ERROR_REPORT_OPENED, CONTACT_OPENED, TUTORIAL_OPENED, REVIEW_OPENED,
    ACCOUNT_SIGNED_OUT, WELCOME_DISMISSED, SUBSCRIBE_LINK_CLICKED,
    PLUGIN_UPDATE_PROMPT_SHOWN, PLUGIN_UPDATE_PROMPT_CLICKED,
    PLUGIN_UPDATE_PROMPT_SUPPRESSED, EXAMPLE_CHOSEN,
    PAIRING_STARTED, PAIRING_FAILED, CONNECTION_FAILED, PLUGIN_UPDATED,
    PLUGIN_UNLOADED, LIBRARY_OPENED, DEMO_DATA_LOADED,
})
