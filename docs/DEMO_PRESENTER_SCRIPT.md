# Demo script: Sarah Tan / ABC Pte Ltd (about 3 minutes)

Two presenters, one laptop mic:

- **Staff** sits at the laptop, drives the buttons and reads the suggestions aloud.
- **Sarah** is the caller. Sarah Tan, 9234 5678, `sarah.tan@gmail.com`.

Everything on screen is generated live from what you say, so say the lines in your own words. Dates come from the
day the demo data was reset: read them off the screen.

## The story

ABC Pte Ltd let Sarah go two weeks after she came back from maternity leave. They said her role was redundant, then
hired a replacement. Her final month's salary (SGD 3,400) and her notice pay haven't been paid. She has called the
centre before, so her record already holds two open cases:

- **CASE-2026-04811, wrongful dismissal.** Claim not filed yet. It must be filed within 1 month of her last day.
- **CASE-2026-04812, unpaid salary.** Her payslips are overdue. The claim must be filed within 6 months of her last
  day.

## Before the audience arrives

- [ ] Server connected (`scripts/gpu.sh connect`), app open at [localhost:3000](http://localhost:3000).
- [ ] Demo data reset, so Sarah's dates are fresh and earlier saved cases are gone.
- [ ] Sit either side of the laptop.
- [ ] Click **Start session**, wait for **On call**, and say a test sentence. Then click **New call** to clear it.

## Rules while speaking

- **Staff speaks first.** The first new voice is labelled Staff.
- Take turns. Don't talk over each other, and leave a short pause after each line.
- **Staff reads the suggestion aloud** as soon as the first words appear; the rest finishes while you read.

## The call

| # | Who | Says (gist) | What the audience sees |
| --- | --- | --- | --- |
| 1 | Staff | "Hello, Employment Advice Centre, how can I help you today?" | |
| | Sarah | "Hi, I called before about being let go by ABC. They still haven't paid me and I don't know what to do next." | Suggestion: ask for her name, number and email |
| 2 | Staff | *Reads the suggestion* | |
| | Sarah | "It's Sarah Tan, nine two three four, five six seven eight, sarah dot tan at gmail dot com." | Caller card fills; her record shows **2 open cases**. The suggestion asks about both pending actions and gives the **date the dismissal claim must be filed by** |
| 3 | Staff | *Reads the suggestion* | |
| | Sarah | "I haven't filed it yet. They let me go two weeks after I came back from maternity leave. They said my role was redundant, then hired someone else. I have the letter, my contract and my last three payslips." | Advice: **TADM wrongful dismissal claim by the recorded date**, plus the documents to send. *Based on* CASE-2026-04811 |
| 4 | Staff | *Reads the suggestion* | |
| | Sarah | "Can I also get back my final month's salary and the notice pay?" | **TADM salary claim within 6 months of her last day**, linked to CASE-2026-04812 |
| 5 | Staff | *Reads the suggestion* | |
| | Sarah | "Thank you. When will you get back to me?" | A follow-up date and a warm close |
| | Sarah | "That works, thanks. Bye." | |
| 6 | Staff | Clicks **End call**, then **Save to case system** | Case note, actions, follow-up and a message to Sarah, drafted from the call; then the saved case |

## Worth pointing out

Use **Pause** to stop the mic while you talk to the audience, then **Resume**.

1. **After beat 2:** it found her from the phone number and went straight to what's urgent.
2. **After beat 3:** the route, the deadline and the documents come together, and **Based on** shows which case
   was used.
3. **After beat 4:** it applied the 6-month limit because she has left her job. Someone still employed would have
   1 year.
4. **After beat 6:** the wrap-up is written for Staff to check and save. The next call from Sarah picks up this case.

## If something goes off-script

| Problem | What to do |
| --- | --- |
| The suggestion differs from the table | Read it anyway: it answers what was actually said |
| Staff and Sarah are swapped | Click **Swap roles**, or click a single turn to switch it |
| The phone number is misheard | Staff types it into the caller card and clicks **Look up** |
| An audience question | Answer it in character: the suggestions follow the conversation |
| You need to start again | **End call**, then **New call** |
