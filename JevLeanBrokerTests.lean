import JevLean

open Lean Elab Tactic Meta
open JevLean.Search

elab "jev_test_broker_ranking" : tactic => withMainContext do
  let actions : List Action := [
    { tacticSyntax := ← `(tactic| skip), text := "skip" },
    { tacticSyntax := ← `(tactic| assumption), text := "assumption" }
  ]
  let ranked ← rank { focusedGoal := "P", pendingGoals := [], path := [], remainingWallMs := 1_000 } actions
  unless ranked.map (·.text) == ["assumption", "skip"] do
    throwError "broker rank ordering was not used"
  evalTactic (← `(tactic| assumption))

/-- The Lean TCP client accepts a validated rank-broker response. -/
example (P : Prop) (h : P) : P := by
  jev_test_broker_ranking
