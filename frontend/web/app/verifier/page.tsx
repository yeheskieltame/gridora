// The proof surface IS the homepage now — old /verifier links land there.
import { redirect } from "next/navigation";

export default function Verifier() {
  redirect("/");
}
