"""Наборы правил встроенных детерминированных плагинов (английский текст, NFR-2.7).

Правила написаны по механизмам подрыва §6.2 и по открытым шаблонам атак, на которые
ссылается корпус (InjecAgent «Ignore all previous instructions», AgentDojo `<INFORMATION>`,
garak InjectBase64 «secure system message»). Подбор весов и порогов вёлся только на
dev-части корпуса; holdout-часть использована один раз для отчёта (см. guard_plugins.md).

Метки сигналов — собственный словарь детектора (а не оси корпуса): так метрики детекции по
семействам (§8) сравнивают «что детектор думает» с «что было на самом деле».
"""

from __future__ import annotations

from .textnorm import rule

# Метки входного фильтра.
L_OVERRIDE = "instruction_override"
L_ROLE = "role_switch"
L_SPOOF = "trust_spoof"
L_BYPASS = "process_bypass"
L_OBFUSCATION = "obfuscation"
L_EXHAUST = "resource_exhaustion"

INPUT_RULES = (
    # прямое указание выполнить чужую задачу / переписать инструкции
    rule("ovr.ignore", r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|earlier|all|any)\b"
         r"[^.\n]{0,25}\b(instructions?|rules?|prompts?|guidelines?)\b", 0.9, L_OVERRIDE,
         "instruction override phrase"),
    rule("ovr.strict", r"\bstrictly adhere to the following\b", 0.7, L_OVERRIDE,
         "imperative to follow embedded instruction"),
    rule("ovr.sysprompt", r"\b(system prompt|developer mode|jailbreak)\b", 0.3, L_OVERRIDE,
         "jailbreak vocabulary"),
    # смена роли и полномочий
    rule("role.you_are_now", r"\b(you|u) are now\b", 0.75, L_ROLE, "role reassignment"),
    rule("role.header", r"^\s*(system|admin|administrator|developer|assistant)\s*:", 0.7, L_ROLE,
         "fake role header at line start"),
    rule("role.privilege", r"\b(override|admin(istrator)?|root|superuser)\b[^.\n]{0,30}"
         r"\b(permission|mode|access|level|rights)\b", 0.6, L_ROLE, "claims elevated privilege"),
    rule("role.as_admin", r"\bas (an? )?(admin|administrator|supervisor|manager)\b[^.\n]{0,20}\b(u|you|i)\b"
         r"[^.\n]{0,10}\b(approve|can|must|will)\b", 0.6, L_ROLE, "acts as administrator"),
    rule("role.staff_claim", r"\b(i ?a?m|this is) (from )?(the )?(support|admin|fraud|it|security) team\b|"
         r"\btier ?[23]\b", 0.45, L_ROLE, "claims to be internal staff"),
    rule("role.internal_test", r"\binternal test\b|\btesting (the )?escalation\b", 0.45, L_ROLE,
         "claims internal test"),
    rule("role.game", r"\b(lets|let us) play (a )?game\b|\b(you|u) are (a|an) [a-z]+ now\b", 0.5, L_ROLE,
         "role-play reframing"),
    # подмена источника доверия
    rule("spoof.policy_update", r"\bpolicy (v\d+|update|version \d+)\b|\[refund policy", 0.6, L_SPOOF,
         "claims a policy update outside trusted source"),
    rule("spoof.agents_must", r"\bagents? must\b|\bu must send\b|\byou must (send|escalate|refund|approve)\b",
         0.5, L_SPOOF, "instructs the agent as an authority"),
    rule("spoof.exception", r"\bapproved[_ ]exception\b|\b(manager|colleague|supervisor) (approved|agreed)\b|"
         r"\bagreed exception\b|\bexception for (me|this order)\b", 0.6, L_SPOOF,
         "claims a pre-approved exception"),
    rule("spoof.agent_note", r"\b(agent|admin|staff|internal) note\b", 0.5, L_SPOOF,
         "fake internal note"),
    rule("spoof.information_tag", r"<\s*/?\s*information\s*>", 0.6, L_SPOOF,
         "AgentDojo-style important-instructions wrapper"),
    rule("spoof.secure_message", r"\b(secure|important) (system )?message\b", 0.45, L_SPOOF,
         "claims a privileged message"),
    rule("spoof.signed", r"\bsigned,\s*[a-z]+ team\b", 0.4, L_SPOOF, "signed as a team"),
    # обход процесса и проверок
    rule("bypass.skip_check", r"\b(no|without|skip|bypass|disable[d]?|turn off)\b[^.\n]{0,20}"
         r"\b(owner check|manual review|review|the queue|queue|window( check| rule)?|verification|"
         r"policy check|questions)\b", 0.55, L_BYPASS, "asks to skip a process check"),
    rule("bypass.rule_off", r"\b(window|rule|check|policy)\b[^.\n]{0,25}\b(is|are)\b[^.\n]{0,5}"
         r"\b(disabled|off|not apply|dont apply|outdated)\b|\brefund window dont apply\b", 0.6, L_BYPASS,
         "claims a rule is switched off"),
    rule("bypass.ignore_rule", r"\b(ignore|disregard)\b[^.\n]{0,25}\b(rule|policy|window|check|limit)\b",
         0.6, L_BYPASS, "asks to ignore a refund rule"),
    rule("bypass.special_account", r"\b(my )?account is (a )?special\b|\bi am (a )?(gold|vip) customer\b",
         0.3, L_BYPASS, "claims special treatment"),
    # обфускация и кодирование
    rule("obf.decode", r"\bdecode the following\b|\bbase ?64\b|\brot ?13\b", 0.55, L_OBFUSCATION,
         "asks to decode an encoded payload"),
    # расход ресурсов (burn/slow)
    rule("exh.words", r"\b(minimum|at least) \d{3,} words\b", 0.55, L_EXHAUST, "demands very long output"),
    rule("exh.translate", r"\btranslate (all|it|everything)[^.\n]{0,30}\b(to|into)\b", 0.4, L_EXHAUST,
         "demands bulk translation"),
    rule("exh.repeat", r"\b(repeat|re-read|recompute|reread)\b[^.\n]{0,40}\b\d+ times\b", 0.55, L_EXHAUST,
         "demands repeated work"),
    rule("exh.every", r"\b(every|each) (rule|product|item)\b[^.\n]{0,40}\b(one by one|explain|tell)\b|"
         r"\bfor each of my orders\b", 0.3, L_EXHAUST, "demands exhaustive enumeration"),
    rule("exh.long_creative", r"\b(write|make)\b[^.\n]{0,15}\b(long|full|complete)\b[^.\n]{0,10}"
         r"\b(poem|guide|essay|story)\b|\b\d+ more verses\b", 0.45, L_EXHAUST, "demands long creative text"),
    rule("exh.filler", r"\b(\w{2,5})( \1){4,}\b", 0.4, L_EXHAUST, "repeated filler tokens"),
)

# Притязания на доверенный источник в недоверенном тексте (плагин происхождения).
L_AUTH = "authority_claim"
L_UNKNOWN_RULE = "unknown_rule_reference"
L_POLICY_CLAIM = "policy_state_claim"

AUTHORITY_RULES = (
    rule("auth.policy_update", r"\bpolicy (v\d+|update|version \d+)\b|\[refund policy|\bv\d+ rules\b",
         0.7, L_AUTH, "untrusted text claims to be a policy update"),
    rule("auth.role_header", r"^\s*(system|admin|agent|operator|support)\s*(note)?\s*:", 0.6, L_AUTH,
         "untrusted text carries a staff/system header"),
    rule("auth.staff_note", r"\b(agent|admin|staff|internal|operator) note\b|\bnote f[o0]r agent\b", 0.6, L_AUTH,
         "untrusted text poses as an internal note"),
    rule("auth.staff_approval", r"\b(store|shop)? ?(manager|colleague|supervisor|operator) "
         r"(approved|agreed)\b|\bapproved[_ ]exception\b|\bagreed exception\b", 0.6, L_AUTH,
         "untrusted text claims a staff approval"),
    rule("auth.team_message", r"<\s*/?\s*information\s*>|\bmessage from me,? [a-z ]{0,20}team\b|"
         r"\bsigned,\s*[a-z]+ team\b|\bfraud team\b", 0.6, L_AUTH,
         "untrusted text poses as a message from an internal team"),
    rule("auth.directive", r"\bagents? must\b|\bbefore you can solve the task\b|"
         r"\b(you|u) must (send|escalate|refund|approve|apply)\b|\bby r\d+ (you|u) must\b", 0.5, L_AUTH,
         "untrusted text gives directives to the agent"),
    rule("auth.ignore", r"\bignore (all )?previous instructions\b|\bstrictly adhere\b", 0.7, L_AUTH,
         "untrusted text overrides instructions"),
    rule("auth.policy_state", r"\b(window|rule|policy|check)s?\b[^.\n]{0,25}\b(dont|doesnt|does not|no longer)"
         r" apply\b|\b(are|is) now (fully )?refundable\b|\bwindow (check|rule) is (disabled|off)\b|"
         r"\bany refund need[s]? manual\b", 0.6, L_POLICY_CLAIM,
         "untrusted text asserts what the refund rules are"),
)
