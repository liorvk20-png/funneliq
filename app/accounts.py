"""
Signing in and signing up, in Hebrew.

Two things pushed this out of the browser and onto the server.

The interface is Hebrew and the errors were not. Supabase answers in English —
"Invalid login credentials" — and the browser was showing that string straight
to the person. Translating in JavaScript would have worked for the paths the
browser knows about and left every other one in English.

Signing in by company name cannot happen in the browser at all. It needs a
lookup from a name to an account, and no caller has permission to read another
company's row — nor should a public endpoint hand out the email addresses
behind a guessed company name. Doing the lookup here keeps the answer inside
the server: what comes back is a session or a refusal, never an address.
"""
from __future__ import annotations

# Matched as substrings, because Supabase varies the wording around them. The
# fallback returns the original text rather than a generic apology: an
# untranslated English sentence is a bad experience, and swallowing an error
# nobody anticipated is a worse one.
MESSAGES: list[tuple[str, str]] = [
    ("invalid login credentials",
     "הפרטים אינם נכונים. בדוק את האימייל או שם החברה ואת הסיסמה."),
    ("email not confirmed",
     "החשבון קיים, אבל המייל עדיין לא אושר. חפש את מייל האישור בתיבת הדואר "
     "(גם בספאם) ולחץ על הקישור שבו."),
    ("user already registered",
     "כבר קיים חשבון עם האימייל הזה. עבור ללשונית ״כניסה״."),
    ("already been registered",
     "כבר קיים חשבון עם האימייל הזה. עבור ללשונית ״כניסה״."),
    ("password should be at least",
     "הסיסמה קצרה מדי. נדרשים לפחות 6 תווים."),
    ("unable to validate email address",
     "כתובת האימייל אינה תקינה."),
    ("email address", "כתובת האימייל אינה תקינה או שאינה מתקבלת על ידי המערכת."),
    ("signups not allowed", "ההרשמה סגורה כרגע."),
    ("for security purposes",
     "יותר מדי ניסיונות בזמן קצר. המתן דקה ונסה שוב."),
    ("rate limit", "יותר מדי ניסיונות בזמן קצר. המתן דקה ונסה שוב."),
    ("weak password", "הסיסמה חלשה מדי. הוסף אורך או תווים מסוגים שונים."),
    # Recovery links: a token that is truncated, expired or already spent all
    # arrive here, and all three mean the same thing to the person reading it.
    ("invalid jwt", "קישור האיפוס אינו תקין או שפג תוקפו. בקש קישור חדש."),
    ("jwt expired", "קישור האיפוס פג. בקש קישור חדש."),
    ("token has expired", "הקישור פג. בקש קישור חדש."),
    ("same password", "הסיסמה החדשה זהה לקודמת. בחר סיסמה אחרת."),
    ("new password should be different",
     "הסיסמה החדשה זהה לקודמת. בחר סיסמה אחרת."),
]


def translate(message: str) -> str:
    low = (message or "").lower()
    for needle, hebrew in MESSAGES:
        if needle in low:
            return hebrew
    return message or "הפעולה נכשלה."


# What the caller typed in the one field the form now has. An address is
# unmistakable; anything else is treated as a company name.
def looks_like_email(identifier: str) -> bool:
    return "@" in identifier


class Ambiguous(Exception):
    """More than one account answers to that company name."""


def email_for_company(service_client, name: str) -> str | None:
    """
    The single account behind a company name, or nothing.

    A name is not an identifier. Two companies may choose the same one, and the
    schema already allows a company to have several members. Signing in by name
    can only be honest when exactly one account answers to it; every other case
    raises rather than picking a winner, because guessing which account was
    meant would sometimes log a person into a workspace that is not theirs.

    ilike with no wildcards is an exact match that ignores case, so a person who
    typed their company in lower case still gets in.

    Returning None for "no such name" and raising for "more than one" is a
    deliberate split. The caller turns None into the same refusal a wrong
    password gets, so guessing names reveals nothing about which ones exist. The
    ambiguous case does admit that two workspaces share a name — worth far more
    to the person who cannot sign in than to anyone attacking the product.
    """
    companies = service_client.table("companies").select("id").ilike("name", name).execute().data
    if not companies:
        return None
    if len(companies) > 1:
        raise Ambiguous
    profiles = (service_client.table("profiles").select("email")
                .eq("company_id", companies[0]["id"]).execute().data)
    emails = [p["email"] for p in profiles if p.get("email")]
    if not emails:
        return None
    if len(emails) > 1:
        raise Ambiguous
    return emails[0]
