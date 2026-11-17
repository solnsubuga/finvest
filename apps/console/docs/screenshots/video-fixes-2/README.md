# Console fixes from the second product video (bugs 13, 14 and 16)

Taken from the console's sample data (`pnpm dev`), in a browser set to US English, which is where the old date fields showed mm/dd/yyyy.

## 16. Date fields read day first

Reports, Deposits and withdrawals, wide:

![Report dates, wide](wide-01-report-dates-day-first.png)

Reports, Deposits and withdrawals, phone:

![Report dates, phone](phone-01-report-dates-day-first.png)

Rates, "Starts on" after typing 3/12/2026 and leaving the field, wide:

![Rates starts on, wide](wide-02-rates-starts-on-typed.png)

Rates, "Starts on", phone:

![Rates starts on, phone](phone-02-rates-starts-on-typed.png)

## 13. Client money check before the first closed day

Reconciliation, wide:

![Client money check, wide](wide-03-client-money-check-before-first-close.png)

Reconciliation, phone:

![Client money check, phone](phone-03-client-money-check-before-first-close.png)

## 14. Settings, the address Finvest sees you on

On a test setup (`/settings?sample=local`), wide:

![Settings test setup, wide](wide-04-settings-test-setup-address.png)

On a test setup, phone:

![Settings test setup, phone](phone-04-settings-test-setup-address.png)

From an office network (unchanged), wide:

![Settings office, wide](wide-05-settings-office-address.png)

From an office network, phone:

![Settings office, phone](phone-05-settings-office-address.png)
