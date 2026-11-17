import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DateInput, readDate, showDate } from "./date-input.tsx";

function Form({ start = "" }: { start?: string }) {
  const [value, setValue] = useState(start);
  return (
    <>
      <label htmlFor="day">Day</label>
      <DateInput id="day" value={value} onChange={(event) => setValue(event.target.value)} />
      <output aria-label="Value">{value}</output>
      <button type="button" onClick={() => setValue("")}>
        Clear
      </button>
    </>
  );
}

describe("readDate", () => {
  it("reads day, month and year, in that order", () => {
    expect(readDate("03/04/2027")).toBe("2027-04-03");
    expect(readDate("3/4/2027")).toBe("2027-04-03");
    expect(readDate("3.4.2027")).toBe("2027-04-03");
    expect(readDate(" 31-12-2026 ")).toBe("2026-12-31");
  });

  it("understands an ISO date", () => {
    expect(readDate("2027-04-03")).toBe("2027-04-03");
  });

  it("gives nothing for a date that is not whole or not real", () => {
    for (const text of ["", "03/04", "03/04/27", "2027-4-3", "31/02/2027", "29/02/2026", "00/01/2027", "01/13/2027", "12/31/2026", "today"]) {
      expect(readDate(text)).toBe("");
    }
    expect(readDate("29/02/2028")).toBe("2028-02-29");
  });

  it("shows an ISO date as dd/mm/yyyy", () => {
    expect(showDate("2027-04-03")).toBe("03/04/2027");
    expect(showDate("")).toBe("");
  });
});

describe("DateInput", () => {
  it("shows the date day first", () => {
    render(<Form start="2027-04-03" />);
    expect(screen.getByLabelText("Day")).toHaveValue("03/04/2027");
    expect(screen.getByLabelText("Day")).toHaveAttribute("placeholder", "dd/mm/yyyy");
  });

  it("gives an ISO date once a whole date is typed, and nothing before", async () => {
    const user = userEvent.setup();
    render(<Form />);
    await user.type(screen.getByLabelText("Day"), "03/04/20");
    expect(screen.getByLabelText("Value")).toHaveTextContent(/^$/);
    await user.type(screen.getByLabelText("Day"), "27");
    expect(screen.getByLabelText("Value")).toHaveTextContent("2027-04-03");
    expect(screen.getByLabelText("Day")).toHaveValue("03/04/2027");
  });

  it("tidies a typed ISO date when the field is left", async () => {
    const user = userEvent.setup();
    render(<Form />);
    await user.type(screen.getByLabelText("Day"), "2027-05-18");
    expect(screen.getByLabelText("Value")).toHaveTextContent("2027-05-18");
    await user.tab();
    expect(screen.getByLabelText("Day")).toHaveValue("18/05/2027");
  });

  it("follows a value changed from outside", async () => {
    const user = userEvent.setup();
    render(<Form start="2027-04-03" />);
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByLabelText("Day")).toHaveValue("");
  });

  it("offers the calendar as a button", () => {
    render(<Form />);
    expect(screen.getByRole("button", { name: "Choose the date from a calendar" })).toBeInTheDocument();
  });
});
