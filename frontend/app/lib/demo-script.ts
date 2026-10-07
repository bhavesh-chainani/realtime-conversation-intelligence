// Scripted call for screen-recorded demos (`/?demo`): played in place of the mic and STT relay.
// Katherine Liao is CUST-0001 in scripts/demo_db.py; the story matches `katherine` in scripts/simulate_call.py.
//
// The call follows the suggestion agent's stages (backend/prompts/suggestion/system.md), and each Staff line is
// close to what the agent suggests after the Customer line before it, so Staff reads as following the screen:
//   identify (phone lookup) -> open case follow-up -> link the new issue (repeat employer, possible retaliation)
//   -> deciding fact (written consent) -> advice: route + deadline -> documents -> close with a dated follow-up.
import type { KnownRole } from "./transcript.ts";

export type DemoLine = {
  role: KnownRole;
  /** As AssemblyAI would transcribe it: numerals for digits and money, so the instant regex picks up the phone number.
   * `{followUp}` becomes the date a week from today (the service guide's follow-up), e.g. "Tuesday 13 October". */
  text: string;
  /** Put the call on hold once the suggestion for this line is on screen, to talk through it. */
  holdAfter?: boolean;
};

export const KATHERINE_SCRIPT: DemoLine[] = [
  {
    role: "staff",
    text: "Good afternoon, Employment Advice Centre, this is Bhavesh speaking. How can I help you today?",
  },
  // Opens with the problem, unidentified: the agent asks for identity first.
  { role: "customer", text: "Hi, my company cut my salary this month and nobody told me why." },
  {
    role: "staff",
    text: "I'm sorry to hear that. May I have your name, contact number and email, so I can check whether we've helped you before?",
  },
  // Phone and email lookup: verified, returning caller, an open leave case with Brightpath.
  {
    role: "customer",
    text: "Sure, it's Katherine Liao. My number is 9123 4567, and my email is katherine.liao@gmail.com.",
  },
  // The agent names the employer on record and the open case rather than asking an open "which company?".
  {
    role: "staff",
    text: "Thanks, Ms Liao, I've found your record. Is this about Brightpath Logistics? I can see your leave case with them is still open.",
  },
  // Repeat employer, weeks after the leave complaint, with written evidence: link the open case, possible retaliation.
  // It also answers the deciding facts the agent would ask next (when, still employed), leaving only written consent.
  {
    role: "customer",
    text: "Yes, Brightpath again, and I'm still working there. They took $300 off this month's pay with no reason, a few weeks after I complained to HR about my leave. And my supervisor sent me a WhatsApp saying people who complain don't get full shifts.",
    holdAfter: true,
  },
  {
    role: "staff",
    text: "That timing matters, so I'll add this to your open leave case as possible retaliation. Did you ever agree in writing to any deductions from your pay?",
  },
  // The last deciding fact (no written consent), and a direct question: the agent answers from the service guide.
  {
    role: "customer",
    text: "No, I never signed anything like that. Can they actually do that? What can I do about it?",
  },
  {
    role: "staff",
    text: "Deductions generally need your written consent, so you can claim the $300 back through a TADM salary claim. As you still work there, you have up to a year, but sooner is better.",
    holdAfter: true,
  },
  { role: "customer", text: "Okay, that's good to know. What do you need from me?" },
  {
    role: "staff",
    text: "Please send us the payslip showing the $300 deduction, a screenshot of your supervisor's message, and your contract if you have it.",
  },
  // Asking what happens next cues the agent to close: what the centre does and a dated follow-up.
  { role: "customer", text: "I can send those tonight. What happens after that?" },
  {
    role: "staff",
    text: "I'll help you prepare the TADM claim and chase Brightpath HR for a written reply. Shall we call you on {followUp} to check it's filed?",
  },
  { role: "customer", text: "Yes, that works. That's everything, thank you so much, Bhavesh." },
  { role: "staff", text: "You're welcome, Ms Liao. Speak to you on {followUp}. Take care." },
];

export const DEMO_SCRIPTS: Record<string, DemoLine[]> = { katherine: KATHERINE_SCRIPT };
