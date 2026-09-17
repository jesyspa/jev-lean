import JevLean

open Lean Elab Tactic Meta
open JevLean.Search

elab "jev_test_restoration" : tactic => withMainContext do
  let _ ← explore [{ tacticSyntax := ← `(tactic| skip), text := "skip" }]
  evalTactic (← `(tactic| assumption))

/-- Failed candidates leave the original proof state available to later candidates. -/
example (P : Prop) (h : P) : P := by
  jev_test_restoration

/-- A closing action must solve every outstanding goal, not just the focused one. -/
example (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  constructor
  jev?

/-- The suggestion emitted by `jev?` is ordinary tactic source that replays directly. -/
example (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  constructor
  all_goals assumption

/-- Suggestions with tactic configuration fields replay without inaccessible-name markers. -/
example (P Q : Prop) : P ∧ Q → Q ∧ P := by
  all_goals aesop (config := { terminal := true, maxRuleApplications := 32 })
