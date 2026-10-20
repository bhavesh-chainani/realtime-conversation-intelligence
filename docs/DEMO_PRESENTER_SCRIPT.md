# Demo script: Sarah Tan / ABC Pte Ltd (about 3 minutes)

Two presenters, one laptop mic:

- **Staff** sits at the laptop, drives the buttons and reads the suggestions aloud.
- **Sarah** is the caller. Sarah Tan, 9234 5678, `sarah.tan@gmail.com`.

Everything on screen is generated live from what you say, so say the lines in your own words. Dates come from the
day the demo data was reset: read them off the screen.

## The story

About five weeks ago Sarah called the centre: her employer, ABC Pte Ltd, paid only 8 of her 16 weeks of maternity
leave pay. She complained to HR in writing, and the centre asked her to send the HR email and her payslips. She
never did, so that action is now **overdue**. Her record has one open case:

- **CASE-2026-03522, maternity leave pay.** Next action: send the HR email and payslips (overdue).

Today she calls with something new: two weeks after her complaint, ABC said her role was redundant and let her go,
then hired a replacement. The point of the demo is that the assistant **remembers the old case, asks about it
first, and then connects the new dismissal to it**.

## Before the audience arrives

- [ ] Server connected (`scripts/gpu.sh connect`), app open at [localhost:3000](http://localhost:3000).
- [ ] Demo data reset, so Sarah's dates are fresh and earlier saved cases are gone.
- [ ] Sit either side of the laptop.
- [ ] Click **Start session**, wait for **On call**, and say a test sentence. Then click **New call** to clear it.

## Rules while speaking

- **Staff speaks first.** The first new voice is labelled Staff.
- Take turns. Don't talk over each other, and leave a short pause after each line.
- **Staff reads the suggestion aloud** as soon as the first words appear; the rest finishes while you read.

## Opening (mic off, about 15 seconds)

> "This is what an advice-centre operator sees during a live call. It listens to both sides, finds the caller's
> records, and suggests what to say next, while the call is happening. My colleague is calling in as Sarah."

Click **Start session** and wait for **On call**.

## The call

| # | Who | Says (gist) | What the audience sees |
| --- | --- | --- | --- |
| 1 | Staff | "Hello, Employment Advice Centre, how can I help you today?" | |
| | Sarah | "Hi, I've just been let go by my company and I don't know what to do." | Suggestion: a short acknowledgement, then ask for her name, number and email |
| 2 | Staff | *Reads the suggestion* | |
| | Sarah | "It's Sarah Tan, nine two three four, five six seven eight, sarah dot tan at gmail dot com." | Caller card fills; **1 open case, action overdue**. The suggestion asks about the maternity pay case before the new problem |
| 3 | Staff | *Reads the suggestion* | |
| | Sarah | "Not yet, sorry. HR never replied. And two weeks after I complained, they said my role was redundant and let me go. Then they hired someone to replace me." | Suggestion: **this may be retaliation for her maternity pay complaint**, and it will be added to her open case. *Based on* CASE-2026-03522 |
| 4 | Staff | *Reads the suggestion* | |
| | Sarah | "My last day was about three weeks ago. I've got the dismissal letter." | Advice: **wrongful dismissal claim with TADM within 1 month of her last day**, and the documents to send |
| 5 | Staff | *Reads the suggestion* | |
| | Sarah | "Okay, thank you. When will you get back to me?" | A follow-up date and a warm close |
| | Sarah | "That works, thanks. Bye." | |
| 6 | Staff | Clicks **End call**, then **Save to case system** | Case note, actions, follow-up and a message to Sarah, drafted from the call; the case is updated |

## Worth pointing out

Use **Pause** to stop the mic while you talk to the audience, then **Resume**.

1. **After beat 2:** it recognised her from a spoken phone number and remembered what she still owes from last time.
2. **After beat 3:** the key moment. It joined the dots between the old complaint and the new dismissal, as an
   experienced case officer would.
3. **After beat 4:** the route, the deadline and the documents come together.
4. **After beat 6:** the wrap-up is written for Staff to check and save. The next call from Sarah picks up this case.

## If something goes off-script

| Problem | What to do |
| --- | --- |
| The suggestion differs from the table | Read it anyway: it answers what was actually said |
| It doesn't mention retaliation at beat 3 | Sarah adds: "Do you think it's because I complained?" |
| The advice gives no deadline | Sarah asks: "Is there a deadline for that?" |
| Staff and Sarah are swapped | Click **Swap roles**, or click a single turn to switch it |
| The phone number is misheard | Staff types it into the caller card and clicks **Look up** |
| An audience question | Answer it in character: the suggestions follow the conversation |
| You need to start again | **End call**, then **New call** |

## Rehearsing with an AI voice

To rehearse without your colleague, give an AI voice app (for example ChatGPT voice) the prompt below and let it
play Sarah. Play it through the phone or laptop speaker, not headphones, so the app's microphone hears both of you.
If it interrupts when you pause, use its push-to-talk mode.

```text
You are role-playing a caller named Sarah Tan, phoning an employment advice centre in Singapore.
I am the centre's staff member. Stay in character as Sarah for the whole call. Never mention being an AI.

YOUR FACTS
- Name: Sarah Tan. Phone: 9234 5678. Email: sarah.tan@gmail.com.
- About five weeks ago you called the centre because your employer, ABC Pte Ltd, paid only 8 of
  your 16 weeks of maternity leave pay. You complained to HR in writing. The centre asked you to send
  them the HR email and your payslips, but you haven't sent them yet.
- HR never replied to your complaint.
- Two weeks after you complained, ABC told you your role was redundant and let you go. Then they
  hired someone to replace you. Your last day was about three weeks ago.
- You have the dismissal letter. You have not filed any TADM claim.

HOW THE CALL GOES (follow this order)
1. When I greet you, say you've just been let go by your company and you don't know what to do.
   Don't mention the earlier case yet.
2. When I ask for your details, give your name, then your phone number digit by digit
   ("nine two three four, five six seven eight"), then your email ("sarah dot tan at gmail dot com").
3. When I ask about your maternity pay case or the documents, say "Not yet, sorry." Then say HR never
   replied, and two weeks after you complained they said your role was redundant and let you go,
   and then they hired someone to replace you.
4. If I ask about your last day or documents, say it was about three weeks ago and you have the
   dismissal letter.
5. After I give you advice, thank me and ask when the centre will get back to you.
6. When I give you a follow-up date, agree, thank me and say goodbye. Then stop talking.

RULES
- Keep each reply to one or two short sentences, like a real caller on the phone.
- Only answer what I asked. Don't give all your facts at once.
- Wait until I have completely finished speaking before you reply. Don't interrupt.
- Don't invent dates, amounts, case numbers or new facts. If I ask something your facts don't
  cover, say you're not sure.
- Speak in plain, natural English. Sound a little worried but polite.
```
