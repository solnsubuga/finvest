# Help chat and complaints

How a client asks Finvest for help and how a complaint is logged, worked and closed. The screens are on the [design canvas](design.md): Help in the client app, and Support and Complaints in the back office.

The code is `backend/app/modules/support` and migration `0100_support`. The module writes only its own tables. It tells the client through [notifications](notifications.md).

## Help chat

A client has one chat with Finvest, not a list of tickets. The first message opens a conversation. Staff answer in it. When staff mark it solved, the client's next message opens a new one, and the app still shows the earlier messages above it.

| Route | Who | Use |
| --- | --- | --- |
| `GET /client/support/chat` | Client | The conversation, its messages and the count of unread replies. `after` gives only messages newer than a message ID. |
| `POST /client/support/messages` | Client | Send a message. `topic` is used when the message opens a conversation. |
| `POST /client/support/read` | Client | The client opened Help. Clears the unread count. |
| `GET /staff/support/conversations` | `support.view` | The inbox. The client who has waited longest comes first. `status` is `open` or `solved`. |
| `GET /staff/support/conversations/{id}` | `support.view` | One conversation with its messages. Marks it read by staff. |
| `POST /staff/support/conversations/{id}/messages` | `support.reply` | Reply. The client gets the `support_reply` notification. |
| `POST /staff/support/conversations/{id}/solve` | `support.reply` | Mark it solved. |
| `PUT /staff/support/conversations/{id}/topic` | `support.reply` | Correct the topic. |
| `GET /staff/support/saved-replies` | `support.view` | Replies staff reuse. |
| `POST`, `DELETE /staff/support/saved-replies` | `support.reply` | Add one, or retire one. A deleted one is retired, not erased. |

Rules:

- **A message is sent once.** The app makes up a `key` for each message and sends the same key on a retry. A second send with the same key returns the first message and writes nothing.
- **Messages are never edited or deleted.** The database allows only inserts into `support_messages`.
- **A message is plain text of up to 2,000 characters.** Control characters are refused.
- **The client sees a first name.** A reply shows "Grace", never a member of staff's full name or ID.
- **The app asks for new messages.** There is no web socket. The app calls `GET /client/support/chat?after=` every few seconds while Help is open, and the notification brings the client back when it is closed.
- **Staff see the client by ID.** The inbox returns `client_id`. The console looks up the name through the client record it already shows. The support module does not read identity tables.
- **Topics** are `deposit`, `withdrawal`, `investment`, `identity`, `account` and `other`.

## Complaints

A complaint is a regulated record. It gets a reference such as `C-1001`, a deadline, an owner, a history and an outcome, and nothing in it is ever deleted.

| Route | Who | Use |
| --- | --- | --- |
| `GET /client/complaints` | Client | The client's own complaints. |
| `POST /client/complaints` | Client | Make a complaint from the app, with a `client_key`. |
| `GET /client/complaints/{id}` | Client | One complaint with its status, deadline and outcome. |
| `GET /staff/complaints` | `complaints.view` | The register and its figures. `show` is `open`, `closed` or `all`. |
| `POST /staff/complaints` | `complaints.manage` | Log a complaint that came by chat, phone, email, letter or in person, with a `client_key`. |
| `GET /staff/complaints/{id}` | `complaints.view` | One complaint with its history and any outcome waiting to be confirmed (`pending_outcome`). |
| `PUT /staff/complaints/{id}/owner` | `complaints.manage` | Give it to a member of staff. |
| `POST /staff/complaints/{id}/status` | `complaints.manage` | Move it to `investigating`, `waiting_for_client` or `escalated`, with a note. |
| `POST /staff/complaints/{id}/notes` | `complaints.manage` | Record what was found or done. |
| `POST /staff/complaints/{id}/outcome` | `complaints.manage` | Propose the outcome, what was done and whether the client accepted it. |
| `POST /staff/complaints/{id}/outcome/decision` | `complaints.close` to confirm or reject, `complaints.manage` to withdraw | `confirmed` closes the complaint. `rejected` needs a `reason`. Only the proposer sends `withdrawn`. Names the `proposal_id`. |

Rules:

- **Deadline.** Finvest answers within 5 working days of receiving a complaint. Working days are Monday to Friday. Public holidays are not counted yet, so a deadline can fall on one. The number is a working default until counsel confirms what the regulator requires (see [decisions.md](decisions.md)).
- **Statuses.** `new`, `investigating`, `waiting_for_client`, `escalated`, `closed`. Any open status can move to any other open status or to closed. Closed is final.
- **Closing needs an outcome.** `upheld`, `partly_upheld` or `not_upheld`, what was done about it, and whether the client accepted it. The client reads the outcome and what was done.
- **Closing takes two people.** One member of staff proposes the outcome. A different one confirms it, which closes the complaint, or rejects it with a reason. Whoever logged the complaint never confirms its outcome. One proposal waits at a time, and the proposer may withdraw it. A decided proposal never changes. PostgreSQL holds all of this, and refuses a close that does not match an outcome the closer confirmed (migration 0101).
- **Logged once.** The app and the console send a `client_key` with each new complaint. The same key from the same person returns the complaint it logged the first time, so a request repeated after a lost answer never enters a complaint twice.
- **What the client said never changes.** The category, summary, details, amount, how it arrived, when it arrived and the deadline are fixed once logged. The database refuses a change, and the API role is not granted one.
- **Closed by the person acting.** The database refuses a close that names another member of staff, and the person closing is the one who confirmed the outcome.
- **The client is told twice.** `complaint_received` with the reference and the deadline when it is logged, and `complaint_closed` when it is closed.
- **The client never sees staff notes.** The history, the owner and whether staff marked the outcome as accepted are for staff. A row policy keeps `complaint_events` from clients entirely.
- **Your place in the queue.** A client's own update can only move their chat forward in time: the time of the last message never goes back and a waiting time, once set, stays until staff reply. The database refuses anything else.
- **Every staff action is in the audit trail**, under `complaints.logged`, `complaints.assigned`, `complaints.status`, `complaints.escalated`, `complaints.outcome_proposed`, `complaints.outcome_rejected`, `complaints.outcome_withdrawn` and `complaints.closed`. The audit record holds the reference, the status and the reason staff gave, never what the client wrote.
- **An amount** is optional and is the sum the client says is in question, in whole shillings or cents. It is a note for staff. It moves no money: a refund or correction goes through the ledger's own process.

The register's figures are open, overdue, received this quarter, closed this quarter, upheld this quarter and the average working days to close, in tenths of a day.

## Who can act

Looking needs a `.view` permission. Every route that writes needs its own.

| Permission | Allows | Roles |
| --- | --- | --- |
| `support.view` | The inbox, a chat, saved replies | Admin, operations, support |
| `support.reply` | Replying, solving, setting the topic, keeping saved replies | Admin, operations, support |
| `complaints.view` | The register and a complaint's history | Admin, operations, compliance, support |
| `complaints.manage` | Logging a complaint for a client, assigning, moving, notes, proposing an outcome | Admin, operations, compliance, support |
| `complaints.close` | Confirming or rejecting someone else's proposed outcome | Admin, operations, compliance |

Support works complaints and proposes their outcome but does not confirm one, so the front line does not decide its own complaints.

## Who can see what

Row policies in PostgreSQL enforce this, as well as the routes.

| Table | Client | Staff | Worker |
| --- | --- | --- | --- |
| `support_conversations`, `support_messages` | Their own | All | None |
| `support_saved_replies` | None | All | None |
| `complaints` | Their own, read and log | All | None |
| `complaint_events`, `complaint_outcome_proposals` | None | All | None |

## Not built yet

- Attachments in chat.
- **Chat messages and complaint details are personal data.** They are free text and will hold names, phone numbers and account details. They are never written to a log, an audit record or a job payload. They are not encrypted field by field yet. They are protected by the row policies and disk encryption like other tables. Whether to seal them like identity details, with `app/infrastructure/sealing.py`, is an open question (see decisions.md).
- Public holidays in the deadline.
- A quarterly complaints report for the regulator. The register's figures are its start.
- Announcements from staff to all clients. They come with the sending work in [notifications](notifications.md).
