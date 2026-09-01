# Getting QuickBooks Online production credentials

How an agent walks its human through registering an Intuit app and producing the five
credential values this skill needs. Roughly 15 minutes, most of it the compliance
questionnaire.

## How you run this

You cannot click through the Intuit portal. Every browser action and every login is
the human's. Your job is a loop: elicit the inputs, hand over precise click-by-click
steps, collect what comes back, and assemble the result into wherever they chose to
keep it.

Steps are tagged **[HUMAN]** for what you relay, and **[AGENT]** for what you do.

## Credential handling, settled before any secret exists

Three of the values are secrets: the **client secret**, the **access token**, and the
**refresh token**. The **client ID** and **realm ID** are identifiers. Less sensitive,
still not for broadcasting.

**Secrets never enter the conversation.** Do not ask the human to paste a secret. Do
not print, echo or repeat one. If they start to paste one, stop them.

**[AGENT] Before the human generates any keys, meaning before Step 5, agree where the
credentials will live and how the values get there.** Offer these, best first:

- **The human types the secrets into the destination themselves.** You supply field
  names or a template with blank values; they fill it in their own editor. You never
  see them. This is the default.
- **A secrets manager.** 1Password, Bitwarden, the OS keychain, a cloud secret store,
  or their CI secret store. This is also what lets the skill run with no credential
  file at all, since it reads the environment first.
- **A local `.env` the integration reads.** The human pastes values in locally; you
  only ever name the path. Confirm it is git-ignored. Write it yourself only if they
  explicitly accept that the secrets pass through your context.

Never paste a secret into chat, email one, put one in a shared document, or commit
one.

You only ever need non-secret confirmations back: "keys generated", "tokens captured",
and the realm ID.

## Before starting

- **[HUMAN] A QuickBooks company admin login.** Creating the app grants no data access
  by itself. Access comes from the consent step (Step 6), which needs an admin of the
  target company. If your human is not one, find who is; that person has to be there
  for Step 6.
- **[HUMAN] A public website URL for the firm.** Intuit asks for EULA, privacy policy,
  host domain and launch URLs. They do not have to be separate pages. One root
  homepage URL is accepted for all of them.
- **[HUMAN] An Intuit developer account.** Free, and existing QuickBooks or TurboTax
  credentials work.

**On scope:** there is no read-only accounting scope. The accounting scope grants read
and write together. Read-only behaviour has to be enforced in the tooling, by shipping
only query operations, and not through OAuth. This is why the questionnaire attests
"reads data and writes data".

## Values to elicit first

Ask for each, then substitute wherever the token appears below.

| Token | Ask for | Used in |
|---|---|---|
| `{{FIRM_LEGAL_NAME}}` | The firm name | Workspace and app naming |
| `{{APP_NAME}}` | What to call the app, internal-facing | The app name in the portal |
| `{{FIRM_WEBSITE_URL}}` | A public `https://` URL, homepage is fine | Every URL field |
| `{{FIRM_HOST_DOMAIN}}` | The bare domain of that URL, no scheme | Host domain |
| `{{PUBLIC_IP}}` | The public IP of the machine that will call the API | Hosting |

**[AGENT] Flag this:** the hosting section wants a single static IP. A home or office
broadband line, or a cloud host, may change it, and the app entry then needs updating.
Capture the IP of the machine that actually makes the calls.

## Step 1 — Intuit developer account · [HUMAN]

1. Go to [developer.intuit.com](https://developer.intuit.com).
2. Sign up, or sign in with existing Intuit credentials.
3. Verify the email if prompted.

Confirm back: signed in.

## Step 2 — Workspace and app · [HUMAN]

1. Open [developer.intuit.com/workspaces](https://developer.intuit.com/workspaces).
   Name the workspace `{{FIRM_LEGAL_NAME}}` and fill in the basic company details.
2. Inside the workspace, add a new app.
3. Set platform to QuickBooks Online (Accounting), name it `{{APP_NAME}}`, and select
   the accounting scope.
4. Under the app's keys and credentials, a development client ID and secret already
   exist. Those are sandbox only. Production keys come in Step 5, so there is nothing
   to capture here.
5. Set the redirect URI to
   `https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl`.
   Step 6 does not work without it.

Confirm back: app created, redirect URI set.

## Step 3 — App details for production · [HUMAN]

In the app's production settings, complete the app details checklist. The same
`{{FIRM_WEBSITE_URL}}` is deliberately reused in every URL field.

| Field | Value |
|---|---|
| End-user license agreement URL | `{{FIRM_WEBSITE_URL}}` |
| Privacy policy URL | `{{FIRM_WEBSITE_URL}}` |
| Host domain | `{{FIRM_HOST_DOMAIN}}` |
| Launch URL | `{{FIRM_WEBSITE_URL}}` |
| Disconnect URL | `{{FIRM_WEBSITE_URL}}` |
| Connect / Reconnect URL | `{{FIRM_WEBSITE_URL}}` |
| Category | Accounting |
| Regulated industries | None of the above |
| Hosting country | The firm's country |
| IP type | Single IP address |
| IP address | `{{PUBLIC_IP}}` |

**The reconnect URL is not optional.** Intuit made it mandatory on 24 February 2026,
alongside the refresh-token expiry change described at the end of this file.

## Step 4 — Compliance questionnaire · [HUMAN], guided by [AGENT]

**[AGENT] The answers below are a worked example that was approved for an internal,
private, desktop bookkeeping app. They are a starting point, not a script to submit
blindly.** This questionnaire is an attestation. Walk the human through every answer
and confirm it is true of their app and their firm.

**General:** no complaints or investigations · no regulatory counsel · security
policies reviewed and confirmed · app enhances QuickBooks · not on sanctions lists ·
no generative AI functionality.

**App information:** built from scratch · desktop app connecting to QuickBooks Online
· reads and writes data · private app · usable by any admin of the company · no
non-Intuit integrations.

**Authorization:** connect, disconnect and reconnect tested in sandbox · refresh only
when the access token expires · retry failed auth · ask the customer to reconnect on
an auth error · discovery document used · handles expired access tokens, expired
refresh tokens and invalid grant · does not handle CSRF · does not rely on the
Playground for tokens.

**API usage:** Accounting API · called monthly per customer.

**Accounting API:** all QuickBooks Online tiers · handles version changes, on the
grounds that the app uses only core Accounting features present across every tier and
handles an unavailable-feature error rather than depending on it · no special features
· no webhooks · no CDC.

**Error handling:** API error handling tested · captures `intuit_tid` from response
headers · errors go to logs · a support route exists.

**Security:** no breach requiring notification · vulnerabilities assessed · client ID
and secret stored securely · no MFA, captcha or WebSocket in the app itself · data not
shared beyond the original customer.

**[AGENT] Confirm these before submitting. They depend on the firm, not on this
template:**

- The two security attestations are only true if the firm has actually done them. Do
  not attest for them.
- Generative AI is answered no because the registered app is deterministic code. The
  agent operating it is not part of the app. Check that framing matches their build.
- Built from scratch, desktop, reads and writes: these have to match reality.
- "Do you rely on the Playground for tokens" is no, because the running integration
  uses stored refresh tokens. The Playground is used once, in Step 6. Make sure the
  human sees the distinction.
- The support and data-sharing answers are phrased for an internal single-firm tool.

## Step 5 — Submit, and collect production keys · [HUMAN]

**[AGENT] The client secret is about to exist. Confirm the storage plan is in place
first.**

1. Submit the questionnaire. Approval for a private app is usually fast.
2. Once approved, open the app's production keys and credentials.
3. Put the **production client ID** and **production client secret** straight into the
   chosen store.

Confirm back: production keys generated and stored. Do not ask for the values.

## Step 6 — First tokens, through the OAuth Playground · [HUMAN]

This step needs the QuickBooks company admin login.

1. Open the OAuth 2.0 Playground. It is launched from the app's dashboard in the
   developer portal, and Intuit documents it at
   [the OAuth 2.0 Playground page](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0-playground).
2. Select the app `{{APP_NAME}}` and make sure production keys are selected.
3. In the scopes selector, choose Accounting.
4. Click **Get authorization code**, then **Authorize**.
5. Sign in to QuickBooks, pick the company, and grant permission.
6. The next section, **Get tokens from authorization code**, fills in with the
   authorization code and the company ID.
7. Get the tokens, then place the **access token**, the **refresh token** and the
   **realm ID** straight into the chosen store.

Confirm back: tokens captured and stored. The realm ID may come to you directly, since
it identifies a company and is not a secret. The two tokens stay out of the
conversation.

The realm ID is also visible in the QuickBooks URL when the company is open.

## Step 7 — Hand off the bundle · [AGENT] and [HUMAN]

Five values plus the environment flag:

| Value | Sensitivity |
|---|---|
| Client ID | Identifier |
| Client secret | **Secret** |
| Access token | **Secret** |
| Refresh token | **Secret** |
| Realm ID | Identifier |
| Environment | `production` |

The variable names this skill reads are in
[`scripts/.env.example`](../scripts/.env.example). Best case, they go into a secrets
manager and reach the skill through the environment, and no file exists. If a `.env`
is used instead, the human confirms it is covered by `.gitignore` at any depth, and it
goes at one of the paths in `SKILL.md` under "Where the skill looks", never inside the
skill directory.

## Token lifetimes

| Token | Lifetime |
|---|---|
| Access token | 1 hour. This skill refreshes it on a 401. |
| Refresh token | Five years maximum, and it rotates. Persist the new value after every refresh. |

**The refresh-token policy changed in November 2025.** Refresh tokens used to be
effectively permanent as long as they were used every 100 days. Intuit replaced that
with a hard five-year maximum. For the accounting scope, tokens issued from October
2023 onward carry the five-year life, and the first of them start expiring in **October
2028**. The refresh response now carries a field naming the expiry date, so an
integration can see it coming. Intuit notifies the customer 30 days out and again at 7
days.

Source: [Important changes to refresh token policy](https://medium.com/intuitdev/important-changes-to-refresh-token-policy-8443779d40db),
Intuit Developer, 12 November 2025.

If the integration reports `REFRESH_TOKEN_EXPIRED`, or the refresh token is lost,
repeat Step 6. That needs a company admin again.

## Troubleshooting

- **The human is not a QuickBooks admin.** They can do Steps 1 through 5. They cannot
  do Step 6. Get an admin for the consent step.
- **No firm website.** Intuit still requires the URL fields. A minimal public page is
  enough.
- **Dynamic or cloud IP.** The single-IP hosting entry drifts. Revisit the app's
  hosting settings if calls start failing on IP grounds.
- **A secret reached the conversation.** Treat it as compromised. Regenerate the
  client secret in the portal, or re-run Step 6 for new tokens. Do not reuse the
  exposed value.

## What was verified, and what was not

Checked against Intuit's current documentation on 2026-08-16.

**Confirmed.** The five-year refresh-token policy, its October 2028 first-expiry date
for the accounting scope, the new expiry field in the refresh response, and the
mandatory reconnect URL from 24 February 2026, all from the Intuit Developer post
cited above. The redirect URI value
`https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl`. That the Playground is
launched from the app dashboard, and that getting the authorization code populates the
token section with the code and the company ID. That `developer.intuit.com/workspaces`
is the live workspaces entry point.

**Not confirmed, and stated loosely here on purpose.** The exact in-portal navigation
labels. Intuit's documentation pages render client-side and could not be read
directly, and the portal's menu wording has already drifted once from the source this
was ported from, which said "My Hub → Workspaces". The steps above name what to reach
rather than the click path, so a label change does not invalidate them.

**The accounting scope string is `com.intuit.quickbooks.accounting`.** Confirmed:
it appears across configs that authorized working production credentials, which a
valid scope does by definition. Some Intuit policy posts shorten it to
`com.quickbooks.accounting`; both name the same scope. The Playground offers it as
a named checkbox, so Step 6 does not depend on the literal; it matters only when
writing the authorization URL by hand.
