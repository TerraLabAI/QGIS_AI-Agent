# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

REGISTRY_VERSION = 50

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
AGENT_ANSWER_RATED = "agent_answer_rated"

AGENT_REPORT_OPENED = "agent_report_opened"

AGENT_REPORT_OFFER_SHOWN = "agent_report_offer_shown"
AGENT_REPORT_OFFER_CLICKED = "agent_report_offer_clicked"
TUTORIAL_OPENED = "tutorial_opened"
REVIEW_OPENED = "review_opened"
ACCOUNT_SIGNED_OUT = "account_signed_out"

SUBSCRIBE_LINK_CLICKED = "subscribe_link_clicked"



PRO_UPSELL_VIEWED = "pro_upsell_viewed"
RUN_LIMIT_REACHED = "run_limit_reached"
PLUGIN_UPDATE_PROMPT_SHOWN = "plugin_update_prompt_shown"
PLUGIN_UPDATE_PROMPT_CLICKED = "plugin_update_prompt_clicked"
PLUGIN_UPDATE_PROMPT_SUPPRESSED = "plugin_update_prompt_suppressed"
EXAMPLE_CHOSEN = "example_chosen"
PAIRING_STARTED = "pairing_started"
PAIRING_FAILED = "pairing_failed"
CONNECTION_FAILED = "connection_failed"


CONNECTION_ESTABLISHED = "connection_established"

PLUGIN_HTTP_TIMED = "plugin_http_timed"
PLUGIN_UPDATED = "plugin_updated"
PLUGIN_UNLOADED = "plugin_unloaded"
LIBRARY_OPENED = "library_opened"


STUDENT_OFFER_SHOWN = "student_offer_shown"
STUDENT_OFFER_CLICKED = "student_offer_clicked"

PLAN_CHANGED = "plan_changed"
CHECKOUT_RETURN_CHECKED = "checkout_return_checked"

AGENT_RUN_STOPPED = "agent_run_stopped"
AGENT_RUN_RETRIED = "agent_run_retried"

NOTICE_SHOWN = "notice_shown"


PROJECT_SAVE_FINISHED = "project_save_finished"
QGIS_QUIT = "qgis_quit"
QGIS_TEMP_FOLDER_RESTORED = "qgis_temp_folder_restored"
PROJECT_OPENED_DEAD_PATHS = "project_opened_dead_paths"

ALL_EVENTS = frozenset({
    PLUGIN_FIRST_OPEN, PLUGIN_OPENED, PLUGIN_ACTIVATED, TELEMETRY_OPT_CHANGED,
    AGENT_RUN_STARTED, AGENT_RUN_ENDED, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED,
    AGENT_TOOL_CALLED, AGENT_PERMISSION_ASKED, AGENT_PERMISSION_DECIDED,
    AGENT_QUESTION_ASKED, AGENT_QUESTION_ANSWERED,
    AGENT_UNDO_USED, PLUGIN_ERROR,
    QUOTA_EXHAUSTED, LOW_BALANCE_CARD_SHOWN, FIRST_RUN_MILESTONE,
    PERMISSION_LEVEL_CHANGED, EFFORT_CHANGED, THREAD_CREATED, THREAD_DELETED, ATTACHMENT_ADDED,
    ERROR_REPORT_OPENED, CONTACT_OPENED, AGENT_ANSWER_RATED, AGENT_REPORT_OPENED,
    AGENT_REPORT_OFFER_SHOWN, AGENT_REPORT_OFFER_CLICKED, TUTORIAL_OPENED, REVIEW_OPENED,
    ACCOUNT_SIGNED_OUT, SUBSCRIBE_LINK_CLICKED,
    PRO_UPSELL_VIEWED, RUN_LIMIT_REACHED,
    PLUGIN_UPDATE_PROMPT_SHOWN, PLUGIN_UPDATE_PROMPT_CLICKED,
    PLUGIN_UPDATE_PROMPT_SUPPRESSED, EXAMPLE_CHOSEN,
    PAIRING_STARTED, PAIRING_FAILED, CONNECTION_FAILED, CONNECTION_ESTABLISHED, PLUGIN_HTTP_TIMED, PLUGIN_UPDATED,
    PLUGIN_UNLOADED, LIBRARY_OPENED,
    STUDENT_OFFER_SHOWN, STUDENT_OFFER_CLICKED,
    PLAN_CHANGED, CHECKOUT_RETURN_CHECKED, AGENT_RUN_STOPPED, AGENT_RUN_RETRIED, NOTICE_SHOWN,
    PROJECT_SAVE_FINISHED, QGIS_QUIT, QGIS_TEMP_FOLDER_RESTORED, PROJECT_OPENED_DEAD_PATHS,
})
