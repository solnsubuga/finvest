// The invented people in the story. None of them is real, and the domain is a test domain.
const ESTHER = {
  given: "Esther",
  family: "Nabukenya",
  email: "esther@finvest-demo.test",
  phone: "0700000418",
  phoneShown: "0700 000 418",
  password: "School-fees-2027!",
  pin: "2580",
  birth: { day: "14", month: "03", year: "1991" },
  nin: "CF91041A2BXQ7K",
  idExpires: "01/08/2029",
  job: "Teacher",
  school: "Kitintale Primary School",
  child: "Daniel",
  nextOfKin: { name: "Peter Ssemwogerere", phone: "0700000927", relation: "Brother" },
  documents: "documents",
};

// A second invented client, set up off camera by setup/second-client.mjs. He pays by bank
// transfer, and Esther sends him part of an investment later in the story.
const MOSES = {
  given: "Moses",
  family: "Okello",
  email: "moses@finvest-demo.test",
  phone: "0700000736",
  phoneShown: "0700 000 736",
  password: "Harvest-plan-2027!",
  pin: "1470",
  birth: { day: "02", month: "09", year: "1985" },
  nin: "CM85092A7LPQ3D",
  idExpires: "15/06/2030",
  job: "Shopkeeper",
  documents: "documents-moses",
};

export const PEOPLE = { esther: ESTHER, moses: MOSES };

// The client the chapters follow. PV_PERSON=moses points the same steps at Moses, which is
// how setup/second-client.mjs signs him up off camera.
export const CLIENT = PEOPLE[process.env.PV_PERSON ?? "esther"];

export const STAFF = {
  ruth: { name: "Ruth Achieng", email: "ruth.achieng@finvest-demo.test", role: "admin" },
  joel: { name: "Joel Mugisha", email: "joel.mugisha@finvest-demo.test", role: "finance" },
  // Joins off camera in chapter 12, as the second admin every team change needs.
  sarah: { name: "Sarah Namatovu", email: "sarah.namatovu@finvest-demo.test", role: "admin" },
  // Invited on camera in chapter 12.
  grace: { name: "Grace Atim", email: "grace.atim@finvest-demo.test", role: "operations" },
};

// Typed into masked fields only, and only on this local copy.
export const STAFF_PASSWORD = "Demo-only-pass-2026!";

// Bank of Uganda Treasury bill auction of 21 Oct 2026 (issue 1236), money market yields
// 11.249%, 13.400% and 14.499%, rounded to whole basis points as rates are stored. Bills pay
// 20% withholding tax. Bonds (28 Oct 2026) appear only on the website: the catalogue has no
// bond product yet.
export const RATES = [
  { code: "TREA-91", name: "91 Day Treasury Bill", rate: "11.25", tax: "20", reason: "BoU auction 21 Oct 2026, issue 1236: 91 day 11.249%" },
  { code: "TREA-182", name: "182 Day Treasury Bill", rate: "13.4", tax: "20", reason: "BoU auction 21 Oct 2026, issue 1236: 182 day 13.400%" },
  { code: "TREA-364", name: "364 Day Treasury Bill", rate: "14.5", tax: "20", reason: "BoU auction 21 Oct 2026, issue 1236: 364 day 14.499%" },
];
