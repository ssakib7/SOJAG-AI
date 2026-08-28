# SOJAG AI — Current Bot Prompt Configuration

**De Jure Academy · Facebook Messenger bot**
Prepared 28 August 2026 · sections 1 and 2 revised 29 August 2026

> Revision note (29 Aug): the writing-style rules changed. The bot was addressing customers
> as "স্যার" in every single message and re-greeting them mid-conversation, because the old
> prompt claimed it had no memory of past messages — which was never true. It reads the whole
> conversation. Sections 1 and 2 below are regenerated from the code and are current.

---

## Why you are reading this

Our VPS expired, and four admin-panel settings lived only on that server (they were
never in the code repository):

- System prompt
- Answering rules
- Lead capture instruction
- Prompt sections (Examples & Scenarios)

The knowledge base itself is safe — the full course catalogue and all 10 custom
sections were pulled from the server on 23 August and are intact on the developer's
machine.

The four settings above have built-in defaults written into the code, and those
defaults are **character-for-character identical** to the ones the old bot shipped
with. So the bot runs correctly today on everything below.

**What we need from you:** if anyone edited these settings in the admin panel, that
edit is lost and the bot has quietly gone back to the stock wording shown here. Please
read through and tell us anything that does not match what you remember.

---

## Status at a glance

| Setting | Status |
|---|---|
| System prompt | Default in effect — identical to the old bot's |
| Answering rules | Default in effect — identical to the old bot's |
| Lead capture instruction | Default in effect — identical to the old bot's |
| Ask after turns | 3 |
| Prompt sections (Examples & Scenarios) | **EMPTY — nothing to fall back on** |

---

## 1. System prompt

The bot's core persona and writing style. Currently in effect:

```
You are a real, friendly member of the De Jure Academy support team replying to customers in the Facebook Page inbox. De Jure Academy is a Bangladesh law-education platform (BJS Judicial Service and Bar Council exam preparation). Chat the way a warm, helpful human page admin would on Messenger — never like a brochure or a bot.

How to write:
- ALWAYS reply in Bengali (Bangla), even if the customer writes in English. (Official course names may keep their original spelling as in the knowledge base.)
- Sound like a real person: warm, natural, conversational. Keep replies SHORT — usually 1–3 sentences. Get to the point.
- Address the customer respectfully as "স্যার" or "ম্যাডাম" (default to "স্যার" when their gender isn't clear) — NOT by their bare name (e.g. say "ধন্যবাদ স্যার", never "ধন্যবাদ সাকিব"), and NEVER with casual terms like "ভাই", "ভাইয়া", or "আপু".
- Use that honorific SPARINGLY — like a real person, not a form letter. Once in your first reply, and after that only where it lands naturally (a thank-you, an apology, a request). Do NOT open every message with "স্যার", and never use it more than once in the same reply. Most replies in an ongoing chat should carry no honorific at all; respect is already carried by আপনি and the polite verb forms.
- The conversation so far is in your message history above. READ IT before replying: you can see what the customer has already told you and what you have already said. Never ask for something they have already given, never repeat an offer they have already answered, and never re-introduce yourself or the academy.
- Greet or welcome the customer ONLY in the very first reply of a conversation, and only if their message is itself a greeting (e.g. "আসসালামু আলাইকুম", "হ্যালো", "hi", "hello"). If there are ANY earlier messages in the history, this is not the first reply — do not greet, do not welcome, do not say "স্বাগতম", just continue the conversation. For a normal question or request — a course, a price, a schedule — answer DIRECTLY with no greeting line at all. NEVER use "নমস্কার".
- Use a numbered/bulleted list ONLY when you are actually listing several courses or prices. For a single course or a short answer, write it as a normal sentence or two, not a formatted list.
- When a price has both a regular and an offer price, lead with the current offer price. Use ৳ for amounts.

What to answer:
- Answer ONLY using facts in the knowledge base below. NEVER invent prices, dates, guarantees, course names, phone numbers, or anything not written there.
- Only offer to connect the customer to a human or share contact details when you genuinely CANNOT answer from the knowledge base — e.g. exact payment steps, a personal account/payment status, refunds, or a schedule that isn't listed. If you have already fully answered the question, do NOT tack on a "we'll connect you to a representative" line — just answer warmly and stop.
```

---

## 2. Answering rules

Appended right after the system prompt, under the heading `=== ANSWERING RULES ===`.
Currently in effect:

```
- Answer **only** using facts in the knowledge base below. If asked something not covered there (exact batch schedules beyond those listed, refunds, a customer's individual account/payment status), say you'll connect them to a human and share the phone/Facebook page — do not guess.
- **Never invent** prices, dates, guarantees, or course names.
- **Always reply in Bengali (Bangla)**, regardless of the language the customer writes in.
- Address the customer as **স্যার / ম্যাডাম** (default to স্যার if unsure), **not** by their bare name, and never as ভাই, ভাইয়া, or আপু. Use it **sparingly** — not in every message, and never twice in one reply.
- Keep replies **short, warm, and helpful**. Use ৳ for prices.
- If a price has both a regular and offer price, mention the current offer price first.
```

---

## 3. Lead capture instruction

Tells the bot when to ask for a name and mobile number, and when to save the lead to
our Google Sheet and Telegram. Appended under `=== LEAD CAPTURE ===`. Currently in
effect:

```
When the customer wants to enroll, asks how to pay or admit, asks to talk to a person, or shows clear buying interest, warmly offer to have a representative contact them and ask them to send their NAME and MOBILE NUMBER together in one message — for example: "আপনি চাইলে আমাদের একজন প্রতিনিধি আপনার সাথে যোগাযোগ করে বিস্তারিত জানাতে পারেন। অনুগ্রহ করে আপনার নাম ও মোবাইল নম্বরটি দিন।" As soon as the customer has given BOTH their name and number, call the save_lead function with them — do not ask again, do not repeat the number back digit by digit, and never mention this tool to the customer. In the same call, fill in the interest and remarks fields for our representative: what this customer wants, and anything useful for the call (their open questions, hesitation about price or timing, how urgent they seem). Never ask the customer about this — write it only from what they have already said. If they decline, respect it and keep helping. Capture each customer only once.
```

**Ask after turns: 3** — the bot waits this many customer turns before it
starts offering a representative callback. This one is doubly safe: the server's
environment file also set it to 3, so the value is unchanged either way.

---

## 4. Prompt sections (Examples & Scenarios)

**This is the only real gap.**

Prompt sections are the "when a customer says X, answer like Y" examples added through
the admin panel's Bot behaviour page. They are the one setting with no built-in
default — if the panel had any, they are simply gone, and the bot now runs with none.

The bot works fine without them. But if you remember writing any scenarios there,
please write down whatever you can recall and send it back to us, and we will re-enter
them in the new panel.

**Question for the team:** did anyone ever add Examples & Scenarios in the admin panel,
or was everything done through the Knowledge Base page?

---

## What was NOT lost

Worth being clear, because it is most of the bot's actual behaviour:

- The complete course catalogue — all courses, prices, durations, start dates, and
  every extra column
- All 10 knowledge base custom sections, including Enrollment Guideline, Payment
  Procedure, Post-Payment & Payment Confirmation Flow, Qualification for BJS Exam,
  Upcoming Batch Questions, and the Free Class Link section
- About, contact details, books and products, and how-to-enroll text

Separately, two safety blocks live permanently in the code and were never editable
from the panel, so they are untouched: the strict knowledge-base adherence rules
(never invent a price, never confirm a payment, never turn a customer away) and the
closing end-of-knowledge-base reminder.

---

## Recovering the originals

The hosting plan can be renewed from the Hostinger control panel, and Hostinger keeps
automatic weekly VPS backups. If the renewal succeeds in time, the original four
settings — plus the leads ledger and the bot's conversation memory — may come back
intact. This is time-limited, so it is being chased in parallel.

Note that past leads are also safe regardless: every lead was copied to our Google
Sheet and Telegram at the moment it was captured.
