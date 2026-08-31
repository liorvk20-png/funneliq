"""
The forty-two rules, as written in the specification.

Adapted in two places, both because the wording rule outranks the template
text. Where the specification's template says "נובע מ", the rule carries the
conditions that licence causal wording — high confidence and a share of at
least 0.60 — so it cannot fire without them; the correlational twin sits below
it at a lower priority and catches everything else. And each template is
phrased for the metric's grammar rather than a fixed verb, so the same rule
reads correctly for עלות and for שיעור.

Priorities are spaced by ten. A rule inserted between two existing ones then
needs no renumbering, and a renumbering is how a rule set silently changes
which sentence a report leads with.
"""
from __future__ import annotations

from app.narrative.engine import Rule

# Shorthand for the two conditions that appear on nearly every rule.
HIGH = {"field": "confidence_label", "op": "eq", "value": "high"}
# A decomposition whose interaction term is large cannot support "the change is
# mostly mix" — the two forces moved together and neither owns the movement.
# Without this, the rule that says so and the rule that attributes 107% to mix
# both fired, in the same report, three lines apart.
STABLE = {"field": "unstable", "op": "eq", "value": False}
# A component can exceed the total change when another component pulls the
# other way. That is real and worth saying, but "107% of the change" is not a
# sentence anyone can read; mix_rate_opposite covers those cases instead.
WITHIN_WHOLE = {"field": "contribution_share", "op": "between", "value": [0.0, 1.0]}
# A segment moving against the overall change is real and needs its own wording.
COUNTER = {"field": "contribution_share", "op": "lt", "value": 0.0}
AT_LEAST_MEDIUM = {"field": "confidence_label", "op": "in", "value": ["high", "medium"]}
DOMINANT = {"field": "contribution_share", "op": "abs_gte", "value": 0.60}


def _r(key, applies_to, priority, conditions, template, section, **kw) -> Rule:
    return Rule(rule_key=key, applies_to=applies_to, priority=priority,
                conditions=conditions, template_he=template, section=section, **kw)


RULES: list[Rule] = [
    # ─────────────────────────────────────────────── A · headline (1-5)
    _r("headline_sharp", "metric_change", 100,
       [{"field": "delta_pct", "op": "abs_gte", "value": 0.30}, HIGH],
       "שינוי חד: {m|label} {m|verb_delta} ב־{delta_pct|pct1}, ל־{value_current|auto}. "
       "מומלץ לבדוק לפני קבלת החלטות תקציב.",
       "headline"),
    _r("headline_declined", "metric_change", 90,
       [{"field": "is_favorable", "op": "eq", "value": False},
        {"field": "delta_pct", "op": "abs_gte", "value": 0.05}],
       "{m|label} {m|verb_delta} ב־{delta_pct|pct1}, ל־{value_current|auto}. "
       "זהו השינוי המשמעותי ביותר בתקופה.",
       "headline"),
    _r("headline_improved", "metric_change", 80,
       [{"field": "is_favorable", "op": "eq", "value": True},
        {"field": "delta_pct", "op": "abs_gte", "value": 0.05}, AT_LEAST_MEDIUM],
       "{m|label} {m|verb_delta} ב־{delta_pct|pct1} לעומת התקופה הקודמת, ל־{value_current|auto}.",
       "headline"),
    _r("headline_stable", "metric_change", 40,
       [{"field": "delta_pct", "op": "lt", "value": 0.05},
        {"field": "delta_pct", "op": "gt", "value": -0.05}],
       "{m|label} יציב יחסית — שינוי של {delta_pct|pct1} בלבד, בתוך הטווח הרגיל.",
       "headline"),
    _r("headline_no_baseline", "metric_change", 120,
       [{"field": "value_baseline", "op": "exists", "value": False}],
       "אין תקופת בסיס להשוואה, ולכן מוצגים ערכים מוחלטים בלבד: "
       "{m|label} עומד על {value_current|auto}.",
       "headline"),

    # ────────────────────────────────────────── B · mix / rate (6-13)
    _r("mix_dominant", "mix_shift", 100,
       [DOMINANT, WITHIN_WHOLE, STABLE, HIGH],
       "השינוי נובע בעיקר משינוי בתמהיל ולא מירידה בביצועים: "
       "{contribution_share|pct1} מהשינוי מוסבר בשינוי בהרכב התנועה.",
       "drivers"),
    _r("rate_dominant", "rate_shift", 100,
       [DOMINANT, WITHIN_WHOLE, STABLE, HIGH],
       "הביצועים עצמם השתנו: {contribution_share|pct1} מהשינוי נובע משינוי "
       "בשיעורים בתוך הפלחים, ולא מהרכב שונה.",
       "drivers"),
    # The correlational twins. Same observation, weaker evidence, no "נובע".
    _r("mix_dominant_soft", "mix_shift", 70,
       [DOMINANT, WITHIN_WHOLE, STABLE],
       "השינוי קשור בעיקר לשינוי בתמהיל ולא לירידה בביצועים: "
       "{contribution_share|pct1} מהשינוי נלווה לשינוי בהרכב התנועה.",
       "drivers"),
    _r("rate_dominant_soft", "rate_shift", 70,
       [DOMINANT, WITHIN_WHOLE, STABLE],
       "השינוי קשור בעיקר לשיעורים בתוך הפלחים: {contribution_share|pct1} "
       "מהשינוי נלווה לשינוי בביצועים ולא להרכב שונה.",
       "drivers"),
    _r("mix_partial", "mix_shift", 50,
       [{"field": "contribution_share", "op": "abs_gte", "value": 0.30},
        {"field": "contribution_share", "op": "lt", "value": 0.60}, STABLE],
       "שינוי בתמהיל קשור לכ־{contribution_share|pct1} מהשינוי הכולל.",
       "drivers"),
    _r("mix_rate_opposite", "mix_shift", 90,
       [{"field": "opposing", "op": "eq", "value": True}],
       "שני כוחות מנוגדים: התמהיל דחף את {m|label} ב־{mix_abs|auto}, "
       "בעוד הביצועים משכו בכיוון ההפוך ב־{rate_abs|auto}.",
       "drivers", requires_fields=("mix_abs", "rate_abs")),
    _r("interaction_high", "mix_shift", 110,
       [{"field": "unstable", "op": "eq", "value": True}],
       "הפירוק בין תמהיל לביצועים אינו חד בתקופה זו — "
       "שני הגורמים זזו יחד באופן מובהק, ולכן לא ניתן לייחס את השינוי לאחד מהם.",
       "drivers"),
    _r("cpa_cpc_driven", "factor_shift", 95,
       [{"field": "effect_type", "op": "eq", "value": "cpc"}, DOMINANT, HIGH],
       "עליית {m|label} מוסברת בעיקר בהתייקרות הקליק ({delta_pct|pct1}), "
       "ולא בהיחלשות ההמרה.",
       "drivers"),
    _r("cpa_cvr_driven", "factor_shift", 95,
       [{"field": "effect_type", "op": "eq", "value": "cvr"}, DOMINANT, HIGH],
       "{m|label} עלתה למרות שמחיר הקליק כמעט לא השתנה — "
       "שיעור ההמרה ירד ב־{delta_pct|pct1}.",
       "drivers"),
    _r("volume_driven", "factor_shift", 90,
       [{"field": "effect_type", "op": "eq", "value": "volume"}, DOMINANT, HIGH],
       "השינוי מוסבר בעיקר בנפח ({delta_pct|pct1}); היעילות עצמה כמעט לא זזה.",
       "drivers"),

    # ──────────────────────────────────── C · segment drivers (14-21)
    _r("driver_single_dominant", "segment_driver", 100,
       [DOMINANT, HIGH, WITHIN_WHOLE],
       "הגורם המרכזי בשינוי ב{m|label_after_b} הוא {d|dim}: "
       "הוא לבדו מסביר {contribution_share|pct1} מהשינוי.",
       "drivers", max_per_report=1),
    _r("driver_single_dominant_soft", "segment_driver", 75,
       [DOMINANT, WITHIN_WHOLE],
       "{d|dim} הוא הפלח הבולט בשינוי ב{m|label_after_b}: "
       "{contribution_share|pct1} מהשינוי נלווים אליו.",
       "drivers", max_per_report=1),
    _r("driver_single_medium", "segment_driver", 60,
       [{"field": "contribution_share", "op": "abs_gte", "value": 0.30},
        {"field": "contribution_share", "op": "lt", "value": 0.60}],
       "{d|dim} תרם כ־{contribution_share|pct1} מהשינוי ב{m|label_after_b}.",
       "drivers", max_per_report=2),
    _r("driver_top_three", "segment_driver", 85,
       [{"field": "top_three_share", "op": "gte", "value": 0.70}],
       "שלושה פלחים מרכזים יחד {top_three_share|pct1} מהשינוי: {top_three_names}.",
       "drivers", requires_fields=("top_three_share", "top_three_names")),
    _r("driver_counter", "segment_driver", 86,
       [COUNTER, {"field": "contribution_share", "op": "abs_gte", "value": 0.30}],
       "{d|dim} נע בכיוון ההפוך לשינוי ב{m|label_after_b} וריכך אותו "
       "בשיעור של {contribution_share|abs_pct1}.",
       "drivers", max_per_report=1),
    _r("driver_overshoot", "segment_driver", 92,
       [{"field": "contribution_share", "op": "abs_gte", "value": 1.0}],
       "{d|dim} לבדו הזיז את {m|label} יותר מהשינוי הכולל "
       "({contribution_abs|auto}) — פלחים אחרים משכו בכיוון ההפוך וקיזזו חלק מכך.",
       "drivers", max_per_report=1),
    _r("driver_diffuse", "segment_driver", 30,
       [{"field": "contribution_share", "op": "lt", "value": 0.20},
        {"field": "contribution_share", "op": "gt", "value": -0.20}],
       "השינוי מפוזר על פני פלחים רבים ואין פלח יחיד שבולט בו. "
       "סביר שמדובר בהשפעה רוחבית.",
       "drivers"),
    _r("driver_offsetting", "segment_driver", 88,
       [{"field": "offset_by", "op": "exists", "value": True}],
       "{d|dim} השתפר ב־{contribution_abs|auto} אך {offset_by} קיזז זאת "
       "ב־{offset_abs|auto}.",
       "drivers", requires_fields=("offset_by", "offset_abs")),
    _r("new_segment", "new_segment", 70, [],
       "פלח חדש הופיע בתקופה זו: {d|dim}, עם {denom_current|num0} רשומות.",
       "watch"),
    _r("disappeared_segment", "disappeared_segment", 70, [],
       "{d|dim} נעלם כמעט לחלוטין — ירידה מ־{denom_baseline|num0} "
       "ל־{denom_current|num0} רשומות.",
       "watch"),
    _r("driver_share_shift", "mix_shift", 65,
       [{"field": "weight_delta", "op": "abs_gte", "value": 0.10}],
       "{d|dim} עבר מ־{weight_baseline|pct1} ל־{weight_current|pct1} מהנפח — "
       "שינוי של {weight_delta|pp1}.",
       "drivers", max_per_report=2,
       requires_fields=("weight_delta", "weight_baseline", "weight_current")),

    # ───────────────────────────────────────────────── D · funnel (22-27)
    _r("funnel_worst_stage", "funnel_dropoff", 100,
       [{"field": "is_worst", "op": "eq", "value": True}],
       "הנטישה הגדולה ביותר היא במעבר {stage_from}→{stage_to}: "
       "{dropoff|pct1} מהמשתמשים נושרים שם.",
       "funnel", requires_fields=("stage_from", "stage_to", "dropoff")),
    _r("funnel_stage_worsened", "funnel_dropoff", 90,
       [{"field": "delta_abs", "op": "lte", "value": -0.05}],
       "שלב {stage_to} החמיר: המעבר ירד ב־{delta_abs|pp1} לעומת התקופה הקודמת.",
       "funnel", max_per_report=2, requires_fields=("stage_to",)),
    _r("funnel_stage_improved", "funnel_dropoff", 80,
       [{"field": "delta_abs", "op": "gte", "value": 0.05}],
       "שלב {stage_to} השתפר ב־{delta_abs|pp1} — השיפור המשמעותי ביותר בפאנל.",
       "funnel", requires_fields=("stage_to",)),
    _r("funnel_bottleneck_value", "funnel_dropoff", 95,
       [{"field": "potential_gain", "op": "gt", "value": 0}],
       "סגירת מחצית מהפער בשלב {stage_to} הייתה מוסיפה כ־{potential_gain|num0} "
       "סגירות בתקופה זו.",
       "funnel", requires_fields=("stage_to", "potential_gain")),
    _r("funnel_segment_gap", "funnel_dropoff", 70,
       [{"field": "segment_gap", "op": "abs_gte", "value": 0.15}],
       "{d|dim} עובר את שלב {stage_to} בשיעור של {segment_rate|pct1} "
       "לעומת {average_rate|pct1} בממוצע.",
       "funnel", requires_fields=("stage_to", "segment_rate", "average_rate")),
    _r("funnel_stable", "funnel_dropoff", 20,
       [{"field": "all_stages_flat", "op": "eq", "value": True}],
       "מבנה הפאנל יציב — אף שלב לא זז ביותר משתי נקודות אחוז.",
       "funnel"),

    # ──────────────────────────────────── E · anomalies, alerts (28-33)
    _r("anomaly_spike", "anomaly", 100,
       [{"field": "z_score", "op": "gt", "value": 3}],
       "חריגה חדה: {m|label} ב־{d|dim} הגיע ל־{value_current|auto} — "
       "חריגה משמעותית מהטווח ההיסטורי.",
       "watch", max_per_report=2, requires_fields=("z_score",)),
    _r("anomaly_drop", "anomaly", 100,
       [{"field": "z_score", "op": "lt", "value": -3}],
       "צניחה חריגה: {m|label} ב־{d|dim} ירד ל־{value_current|auto}.",
       "watch", max_per_report=2, requires_fields=("z_score",)),
    _r("anomaly_cluster", "anomaly", 110,
       [{"field": "cluster_size", "op": "gte", "value": 3}],
       "זוהו {cluster_size|num0} חריגות בתקופה קצרה — דפוס שמצביע לרוב על בעיה "
       "טכנית או שינוי במעקב, ולא על שינוי התנהגותי.",
       "watch", requires_fields=("cluster_size",)),
    _r("threshold_breach", "threshold_breach", 105, [],
       "{m|label} חצה את הסף שהגדרת ({threshold|auto}) ועומד כעת "
       "על {value_current|auto}.",
       "watch", max_per_report=2, requires_fields=("threshold",)),
    _r("anomaly_recovered", "anomaly", 50,
       [{"field": "recovered_after_days", "op": "exists", "value": True}],
       "החריגה ב־{d|dim} חזרה לטווח הרגיל לאחר {recovered_after_days|num0} ימים.",
       "watch", requires_fields=("recovered_after_days",)),
    _r("saturation_point", "saturation_point", 95, [],
       "{d|dim} מתקרב לרוויה: מעבר ל־{spend_threshold|money} "
       "התשואה השולית יורדת משמעותית.",
       "watch", requires_fields=("spend_threshold",)),

    # ───────────────────────────────── F · data quality, sample (34-38)
    _r("small_sample_warning", "small_sample", 100, [],
       "{d|dim} מוצג ללא מסקנה — {denom_current|num0} רשומות בלבד, "
       "מעט מדי להסקה סטטיסטית.",
       "quality", max_per_report=2),
    _r("data_gap", "data_quality", 95,
       [{"field": "missing_periods", "op": "gt", "value": 0}],
       "חסרים נתונים ב־{missing_periods|num0} תקופות בטווח הנבדק. "
       "ההשוואה עשויה להיות מוטה.",
       "quality", requires_fields=("missing_periods",)),
    _r("data_quality_score", "data_quality", 90,
       [{"field": "dq_score", "op": "lt", "value": 80}],
       "ציון איכות הנתונים לדאטהסט זה הוא {dq_score|num0} מתוך 100. "
       "מומלץ לטפל בכך לפני קבלת החלטות תקציב.",
       "quality", requires_fields=("dq_score",)),
    _r("partial_period", "data_quality", 100,
       [{"field": "days_elapsed", "op": "exists", "value": True}],
       "התקופה הנוכחית עדיין לא הושלמה ({days_elapsed|num0} מתוך "
       "{days_total|num0} ימים). ההשוואה חלקית.",
       "quality", requires_fields=("days_elapsed", "days_total")),
    _r("duplicate_rows", "data_quality", 85,
       [{"field": "duplicate_count", "op": "gt", "value": 0}],
       "זוהו {duplicate_count|num0} רשומות כפולות בקובץ שהועלה.",
       "quality", requires_fields=("duplicate_count",)),

    # ──────────────────────────────────── G · forecast and pacing (39-42)
    _r("pacing_on_track", "pacing_risk", 60,
       [{"field": "gap", "op": "abs_gte", "value": 0},
        {"field": "gap", "op": "lte", "value": 0.05},
        {"field": "gap", "op": "gte", "value": -0.05}],
       "הקצב תואם ליעד: התחזית לסוף התקופה היא {forecast|auto} "
       "מול יעד {target|auto}.",
       "watch", requires_fields=("forecast", "target", "gap")),
    _r("pacing_behind", "pacing_risk", 100,
       [{"field": "gap", "op": "lt", "value": -0.05}],
       "פיגור מול היעד: בקצב הנוכחי צפוי {forecast|auto} מול יעד {target|auto} — "
       "פער של {gap|pct1}.",
       "watch", requires_fields=("forecast", "target", "gap")),
    _r("pacing_ahead", "pacing_risk", 80,
       [{"field": "gap", "op": "gt", "value": 0.05}],
       "הקדמה מול היעד: התחזית היא {forecast|auto}, "
       "כ־{gap|pct1} מעל היעד.",
       "watch", requires_fields=("forecast", "target", "gap")),
    _r("forecast_miss", "forecast_miss", 70, [],
       "התחזית הקודמת החטיאה ב־{miss|pct1}. רווח הביטחון הורחב בהתאם.",
       "quality", requires_fields=("miss",)),
]

RULES_BY_KEY = {r.rule_key: r for r in RULES}
