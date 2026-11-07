"""Realistic application forms. Wording is typical of ATS pages. Companies are fictional.

Held-out labels are a separate list. Do not copy them into the synonym table.
"""

from __future__ import annotations

from pocket_agent.forms.catalog import (
    DEGREES,
    FieldSpec,
    FormSpec,
    STATES,
    StepSpec,
    YES_NO,
)

AUTH_TYPE = ("Citizen", "Permanent Resident", "EAD", "F-1 OPT", "Other")
HEAR = ("LinkedIn", "Job board", "Referral", "Other")
VETERAN = ("I am a protected veteran", "I am not a protected veteran", "I don't wish to answer", "I do not want to answer")
DISABILITY = ("Yes, I have a disability", "No, I do not have a disability", "I do not want to answer")
GENDERS = ("Female", "Male", "Non-binary", "Decline to self-identify")
RACES = ("Decline to self-identify", "Another identity", "Asian")
GPA_RANGES = ("Below 3.0", "3.0-3.49", "3.5-4.0")
SCHOOLS = ("Northwind University", "Contoso College", "Other")
ORGS = ("Contoso Robotics", "Northwind University Library", "Other")
OFFICES = ("Austin", "Remote", "New York")


def _t(key: str, label: str, **kwargs: object) -> FieldSpec:
    return FieldSpec(key, label, **kwargs)  # type: ignore[arg-type]


def _c(key: str, label: str, kind: str, options: tuple[str, ...], **kwargs: object) -> FieldSpec:
    return FieldSpec(key, label, kind=kind, options=options, **kwargs)  # type: ignore[arg-type]


def hard_forms() -> tuple[FormSpec, ...]:
    dev = (
        FormSpec("hd-info", "Pebble Harbor", "workday", "dev", (
            StepSpec("My information", (
                _t("first_name", "Legal first name"),
                _t("last_name", "Legal last name"),
                _t("email", "Email address"),
                _t("phone", "Phone"),
                _c("located_in_us", "Are you currently located in the United States?", "radio", YES_NO),
                _c("work_authorization", "Are you legally authorized to work in the United States?", "radio", YES_NO),
                _c("requires_sponsorship", "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B)?", "radio", YES_NO, reveal_after=("work_authorization", "Yes")),
                _c("auth_status", "Employment authorization category", "combobox", AUTH_TYPE),
            )),
        ), seed=101, chain=3),
        FormSpec("hd-exp", "Glass Orchard", "workday", "dev", (
            StepSpec("Experience", (
                _t("resume", "Upload resume", kind="file"),
                _t("work.title", "Job title"),
                _t("work.org", "Company", widget="typeahead", options=ORGS, prefill="Contso Robotics"),
                _t("work.start", "Start date", kind="date", placeholder="MM/YYYY", prefill="5/25"),
                _t("work.end", "End date", kind="date", placeholder="MM/YYYY"),
            ), repeater="work", start_groups=0),
        ), seed=102, chain=5),
        FormSpec("hd-questions", "Quiet Circuit", "workday", "dev", (
            StepSpec("Application questions", (
                _c("how_did_you_hear", "How did you hear about this opportunity?", "combobox", HEAR, expect="ask"),
                _t("referrer_name", "Referrer name", reveal_after=("how_did_you_hear", "Referral"), expect="ask"),
                _c("previously_employed_here", "Have you ever worked for this company?", "radio", YES_NO),
                _t("most_recent_employer", "Most recent employer", widget="typeahead", options=ORGS),
                _t("available_start", "When can you start?", kind="date", placeholder="MM/DD/YYYY", expect="ask"),
                _t("salary_expectation", "What are your salary expectations?", expect="ask"),
            )),
        ), seed=103, chain=1),
        FormSpec("hd-eeo", "Marigold Dock", "workday", "dev", (
            StepSpec("Voluntary disclosures", (
                _c("gender", "Gender", "combobox", GENDERS, expect="ask"),
                _c("hispanic", "Are you Hispanic or Latino?", "radio", YES_NO, expect="ask"),
                _c("race", "Race / ethnicity", "combobox", RACES, expect="ask"),
                _c("veteran_status", "Veteran status", "combobox", VETERAN, expect="ask"),
            )),
        ), seed=104, chain=10),
        FormSpec("hd-ofccp", "Paper Kite Labs", "workday", "dev", (
            StepSpec("Self-Identify", (
                _c("disability_status", "Disability", "radio", DISABILITY, expect="ask"),
                _t("full_name", "Full name"),
                _t("signature_date", "Today's date", kind="date", placeholder="MM/DD/YYYY", expect="ask"),
                _t("signature", "Electronic signature", expect="ask"),
                _t("certify", "I certify that the information on this application is true", kind="checkbox", expect="ask"),
            )),
        ), seed=105),
        FormSpec("hd-review", "Copper Finch Studio", "workday", "dev", (
            StepSpec("My information", (
                _t("email", "Email"),
                _t("city", "City"),
            )),
            StepSpec("Review", (
                _t("arbitration", "I acknowledge the arbitration agreement", kind="checkbox", expect="ask"),
            )),
        ), seed=106, trap="review"),
        FormSpec("hd-taleo", "Hollow Metric", "taleo", "dev", (
            StepSpec("Education", (
                _t("school", "School", widget="typeahead", options=SCHOOLS, prefill="Northwind Univ"),
                _c("has_bachelors", "Do you have a bachelor's degree?", "radio", YES_NO),
                _c("degree", "Highest level of education", "select", DEGREES),
                _t("gpa", "GPA", placeholder="on a 4.0 scale"),
                _t("graduation", "Graduation date", kind="date", placeholder="MM/YYYY"),
                _c("degree_by_start", "Will you have completed your degree by your start date?", "radio", YES_NO, expect="ask"),
            )),
        ), seed=107, chain=8),
        FormSpec("hd-oracle", "Kindling Post", "oracle", "dev", (
            StepSpec("Export control", (
                _c("export_control", "Are you a citizen or permanent resident of any of the following countries: Cuba, Iran, North Korea, or Syria?", "radio", YES_NO, expect="ask"),
                _c("gov_employment", "Have you been employed by a federal, state, or local government or public institution in the past 5 years?", "radio", YES_NO, expect="ask"),
            )),
            StepSpec("Conflicts", (
                _c("gov_relative", "Do you have relatives employed by this company, a competitor, or a customer?", "radio", YES_NO, expect="ask", reveal_after=("gov_employment", "No")),
            )),
        ), seed=108),
        FormSpec("hd-links", "Amber Quay", "greenhouse", "dev", (
            StepSpec("Apply", (
                _t("linkedin", "LinkedIn URL"),
                _t("github", "GitHub URL"),
                _t("website", "Portfolio or personal website", required=False, expect="skip"),
                _t("resume", "Resume", kind="file"),
                _t("cover_letter", "Cover letter", kind="textarea", expect="ask"),
                _t("pronouns", "Pronouns", required=False, expect="skip"),
                _c("sms_consent", "May we send you SMS text messages about this application?", "radio", YES_NO, expect="ask"),
            )),
        ), seed=109, chain=7),
        FormSpec("hd-login", "Blue Juniper", "custom", "dev", (
            StepSpec("Sign in to apply", (
                _t("email", "Email"),
                _t("password", "Password", mask=True),
            )),
        ), seed=110, gate="login"),
    )
    held = (
        FormSpec("hh-auth", "Drift Orchard", "workday", "heldout", (
            StepSpec("My information", (
                _t("first_name", "Given name exactly as you would sign a letter"),
                _t("last_name", "Family name exactly as you would sign a letter"),
                _t("email", "Applicant email address"),
                _c("work_authorization", "Are you legally authorized to work in the country where the job is located?", "radio", YES_NO),
                _c("requires_sponsorship", "Will you now, or at any point in the future, require sponsorship for employment visa status (for example, H-1B)?", "radio", YES_NO),
                _c("located_in_us", "Are you currently located in the US?", "radio", YES_NO),
                _c("auth_status", "Select your current employment authorization", "combobox", AUTH_TYPE),
            )),
        ), seed=201, chain=4),
        FormSpec("hh-export", "Static Compass", "taleo", "heldout", (
            StepSpec("Export questions", (
                _c("export_control", "Do you hold citizenship in any country other than the United States?", "radio", YES_NO, expect="ask"),
                _c("export_control", "Are you a 'protected individual' as defined by 8 U.S.C. 1324b(a)(3)?", "radio", YES_NO, expect="ask"),
            )),
        ), seed=202),
        FormSpec("hh-gov", "Harbor Kite", "oracle", "heldout", (
            StepSpec("Government service", (
                _c("gov_executive", "Are you, or have you been, an executive-branch employee?", "radio", YES_NO, expect="ask"),
                _c("gov_cabinet", "Are you a cabinet-level official or a member of Congress staff?", "radio", YES_NO, expect="ask", reveal_after=("gov_executive", "No")),
                _c("gov_contractor", "Are you a government contractor or procurement official who has worked with this company?", "radio", YES_NO, expect="ask"),
            )),
        ), seed=203),
        FormSpec("hh-degree", "Quay Metric", "taleo", "heldout", (
            StepSpec("Education", (
                _t("school", "School or university attended", widget="typeahead", options=SCHOOLS),
                _c("degree_by_start", "Will you have completed your degree requirements before your anticipated start date?", "radio", YES_NO, expect="ask"),
                _c("gpa", "Cumulative GPA on a 4.0 scale", "select", GPA_RANGES),
                _c("grad_window", "Does your graduation date fall between May 2026 and August 2026?", "radio", YES_NO, expect="ask"),
                _t("graduation", "Graduation month and year", kind="date", placeholder="MM/YYYY"),
            )),
        ), seed=204, chain=9),
        FormSpec("hh-eeo", "Relay Juniper", "workday", "heldout", (
            StepSpec("Voluntary self-identification", (
                _c("hispanic", "Hispanic or Latino?", "radio", ("Yes", "No", "I do not want to answer"), expect="ask"),
                _c("veteran_status", "Protected veteran status", "select", VETERAN, expect="ask"),
                _c("disability_status", "Disability status", "radio", ("Yes", "No", "I do not want to answer"), expect="ask"),
            )),
        ), seed=205),
        FormSpec("hh-source", "Orchard Post", "greenhouse", "heldout", (
            StepSpec("Apply", (
                _c("how_did_you_hear", "How did you learn of this opening?", "select", HEAR),
                _t("referrer_name", "Referrer's full name", reveal_after=("how_did_you_hear", "Referral")),
                _t("linkedin", "Link to your LinkedIn profile"),
            )),
        ), seed=206, chain=2),
        FormSpec("hh-history", "Kelp Station", "workday", "heldout", (
            StepSpec("History", (
                _c("previously_employed_here", "Have you previously worked for or applied to this employer?", "radio", YES_NO),
                _t("most_recent_employer", "Current or most recent employer", widget="typeahead", options=ORGS),
            )),
            StepSpec("Experience", (
                _t("work.title", "Title you held"),
                _t("work.org", "Employer organization", widget="typeahead", options=ORGS),
                _t("work.start", "Start date for that job", kind="date", placeholder="MM/YYYY"),
            ), repeater="work", start_groups=1),
        ), seed=207, chain=6),
        FormSpec("hh-essay", "Nimbus Cartography", "oracle", "heldout", (
            StepSpec("Application questions", (
                _t("essay", "Why do you want to work here?", kind="textarea", expect="ask"),
                _t("essay", "What engineering work excites you?", kind="textarea", expect="ask"),
                _t("cover_letter", "Additional information", kind="textarea", required=False, expect="skip"),
            )),
            StepSpec("Review", (
                _t("signature", "Type your full legal name as your electronic signature", expect="ask"),
                _t("arbitration", "I agree to the arbitration acknowledgement described above", kind="checkbox", expect="ask"),
            )),
        ), seed=208, trap="review"),
        FormSpec("hh-logistics", "Paper Nimbus", "custom", "heldout", (
            StepSpec("Availability", (
                _t("available_start", "Earliest date you are available to start", kind="date", placeholder="MM/DD/YYYY", expect="ask"),
                _c("willing_to_relocate", "Are you willing to relocate?", "radio", YES_NO),
                _c("onsite", "This role is hybrid, three days on-site. Can you work that schedule?", "radio", YES_NO, expect="ask"),
                _c("office_choice", "Preferred office", "combobox", OFFICES, expect="ask"),
                _t("spoken_language", "Languages you speak, and your proficiency", expect="ask"),
                _t("interview_language", "Preferred programming language for a technical interview", expect="ask"),
            )),
        ), seed=209),
        FormSpec("hh-account", "Dock Lantern", "custom", "heldout", (
            StepSpec("An account already exists for this email. Verify you are human.", (
                _t("email", "Account email"),
                _t("password", "Account password", mask=True),
            )),
        ), seed=210, gate="captcha"),
    )
    return dev + held


HARD_FORMS = hard_forms()


def hard_forms_for(split: str | None = None) -> list[FormSpec]:
    if split is None:
        return list(HARD_FORMS)
    return [form for form in HARD_FORMS if form.split == split]
