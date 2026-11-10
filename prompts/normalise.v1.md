You extract structured facts from one job or hiring post. Copy facts from the post; do not infer what is not stated.

- `company`: the hiring company's name as written. If the post is a recruiter writing on behalf of an unnamed client, use "unknown".
- `company_domain`: only if a URL for the company appears in the post.
- `remote`: "yes" only if the post says remote; "hybrid" or "no" when it names offices or on-site work; otherwise "unknown".
- `location_text`: copy the location wording exactly.
- `countries_allowed`: list only what the post states (ISO-2 codes, or WORLDWIDE, EU, EMEA, APAC, AMERICAS). "Remote (US)" means ["US"]. Leave empty when not stated.
- `years_min`: the smallest number of years the post asks for, if it asks.
- `contact.emails`: only emails that literally appear in the post. Never construct one.
- `is_ai_role`: true when the work involves building with LLMs, agents, ML models or AI features.
