# Account numbers

Every client gets a Finvest account number. It is separate from the internal ID the system uses.

| Rule | Detail |
| --- | --- |
| Format | 10 digits, for example 1001 004 827 |
| First 2 digits | Account type: 10 personal, 20 joint, 30 company, 40 group or SACCO |
| Next 7 digits | Random, not in sequence, so nobody can guess a neighbour's number or count clients |
| Last digit | A Luhn check digit, the method banks use for card numbers. A mistyped number is caught before any money is matched to the wrong client. |
| When it is issued | When the identity check passes. A person can hold more than one account, for example personal and a group. |
| Lifetime | Never changes and is never reused, even after the account is closed |
| How it is generated | In the database, inside the same transaction that approves the client, with a uniqueness rule and a retry if a number is already taken |
| Where clients see it | The welcome screen after verification, Profile, receipts, statements, tax certificates, and as the reference for bank transfers |
| Migration | Existing clients get a new number. Their old number is kept as an alternative reference, so old bank instructions still match. |

Display groups the digits as 4, 3, 3. Storage and matching use the 10 digits with no spaces.
