# Product types

The owner decided on 2026-11-15 that the firm may invent products: staff define a product type, such as Corporate bond or Fixed deposit, and build it on a way of earning the system already pays correctly (see [decisions.md](decisions.md)). The code is `backend/app/modules/product_types`, the pure rules are in its `domain/types.py`, and the database side is migration 0493.

## What a type is

| Field | What it is |
| --- | --- |
| Code | What staff and the API call it, such as `CORP-BOND`. Capital letters, digits and hyphens. Never changes. |
| Return model | The code that pays it. Never changes. |
| Name | What clients see, such as Corporate bond. Unique, whatever the case. |
| Description | What staff read when choosing it. |
| Usual tax | The tax a new product or issue of the type starts with. Staff may enter another on each. Clients never see it. |
| Status | Active, or retired: a retired type takes no new products or issues, and those that exist carry on. |

There are three return models. A new way of earning, such as a daily unit price for unit trusts or interest paid quarterly, needs code before a type can use it.

| Model | How it earns | Built-in type |
| --- | --- | --- |
| `treasury_bill` | A rate before tax over a term in days, paid at maturity. Bought at the auction at a discount. | Treasury bill (`TREASURY-BILL`) |
| `fixed_income` | A rate before tax over a term, or an open term, paid at maturity or monthly. | Fixed income (`FIXED-INCOME`) |
| `treasury_bond` | An issue with its own ISIN, coupon and dates, bought at a price, paying a coupon every six months and the face value at maturity. See [bonds.md](bonds.md). | Treasury bond (`TREASURY-BOND`) |

The built-in types are seeded by the migration, and every product and bond issue that existed before it has the built-in type of its kind.

## Changing types

One member of staff proposes, with a reason, and a different one approves or sends back with a note. The proposer may withdraw. One change waits per type at a time. A change is one of four: a new type, an edit of its name, description and usual tax, retiring it, or restoring it. Editing the usual tax changes no product that already exists.

## Products and issues

* A product's kind is its type's model. A new product names its type with `type_code`, or takes the built-in type of its kind. A draft may move to another type of its kind, and a product that has left draft keeps its type, as it keeps its kind.
* A new bond issue names a type of the bond model, Treasury bond by default. An issue's type never changes.

## What the database enforces

* A type is written only by an approval, by the member of staff the transaction acts for, with exactly the values approved. Its code, model and whether it is built in never change, and no type is removed.
* A change is proposed by the person signed in, decided once by someone else, and never removed.
* An approval that wrote nothing is refused at commit.
* A product change takes a type of its own kind, and one in use unless it keeps the product's type. A product has the type its approved change names.
* A new bond issue takes a bond type in use, and has the type its approved change names.

## HTTP routes

| Route | Who | What |
| --- | --- | --- |
| `GET /staff/product-types` | `products.view` | Every type with its model, usual tax, status and how many products and issues use it, and the return models a new type can be built on. |
| `GET /staff/product-type-changes?status=` | `products.view` | Changes, newest first. |
| `POST /staff/product-type-changes/new-type`, `/edit`, `/status` | `products.change` | Propose a new type, an edit, or retiring (`retired`) or restoring (`active`) one. |
| `POST /staff/product-type-changes/{id}/approve`, `/send-back`, `/withdraw` | `products.change` | Deciding. Approving asks the person to confirm it is them. |

`GET /staff/products` shows each product's `type_code` and `type_name`. `POST /staff/product-changes/new-product`, `/new-terms` and `POST /staff/bond-changes/new-issue` take an optional `type_code`.

Audit events on resource `product_type_change`: `product_types.change.proposed`, `.approved`, `.sent_back` and `.withdrawn`, with the type's code, model, name and usual tax.

## In the console

Products lists the types under the bonds, with how each earns, its usual tax, how many products use it and whether it is in use or retired. Staff with `products.change` define a new type (`/products?new-type`), choosing the return model first, and change, retire or restore one (`/products?edit-type=CODE`). Each is a change a second person approves or sends back on the same screen.

New product starts with a Type list of the types in use. The type's return model decides which form follows (a bond type opens the bond form), its usual tax fills the tax field, and its code goes with the new product. A link under the list opens New product type. If the types cannot be loaded, the list falls back to the three built-in types and the backend picks the type from the kind.

## Not built yet

The types in the console approvals inbox, and showing the type's name to clients.
