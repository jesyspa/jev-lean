import JevLean

open Lean Elab Tactic Meta
open JevLean.Search

private def controlledActions : ActionSource := do
  return (← catalogue).filter fun action =>
    action.text.startsWith "intro " || action.text == "constructor" ||
      action.text.startsWith "exact " || action.text.startsWith "apply "

private def identityRanker : ActionRanker := fun _ actions => pure actions

private def aesopFirst : ActionRanker := fun _ actions =>
  pure <| actions.mergeSort fun left right =>
    left.text.startsWith "aesop" && !right.text.startsWith "aesop"

private def reverseRanker : ActionRanker := fun _ actions => pure actions.reverse

elab "jev_test_restoration" : tactic => withMainContext do
  let root ← initialNode
  let _ ← expand root [{ tacticSyntax := ← `(tactic| skip), text := "skip" }]
  evalTactic (← `(tactic| assumption))

elab "jev_test_structural_path" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 4, maxCost := 4 } controlledActions identityRanker
    | throwError "controlled search found no path"
  let texts := node.path.map (·.text)
  unless texts == ["intro jev_h", "constructor", "exact jev_h", "exact jev_h"] do
    throwError "unexpected controlled path: {texts}"
  unless node.goals.isEmpty && node.depth == 4 && node.cost == 4 do
    throwError "closing node did not retain its goals, depth, and cost"
  replay node.path

elab "jev_test_apply_path" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 2, maxCost := 2 } controlledActions identityRanker
    | throwError "controlled apply search found no path"
  let texts := node.path.map (·.text)
  unless texts == ["apply h", "exact hp"] do
    throwError "unexpected apply path: {texts}"
  replay node.path

elab "jev_test_aesop_rank_seam" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 2, maxCost := 2 } catalogue aesopFirst
    | throwError "aesop-first search found no path"
  unless node.path.head?.map (·.text.startsWith "aesop") == some true do
    throwError "injected rank order was not respected"
  replay node.path

elab "jev_test_nonclosing_retained" : tactic => withMainContext do
  let root ← initialNode
  let successors ← expand root (← controlledActions)
  unless successors.any fun node => node.path.map (·.text) == ["intro jev_h"] && !node.goals.isEmpty do
    throwError "a non-closing intro transition was discarded"
  evalTactic (← `(tactic| intro h; exact h))

elab "jev_test_budget_exhaustion" : tactic => withMainContext do
  unless (← searchWith { maxNodes := 0 } controlledActions identityRanker).isNone do
    throwError "node budget did not stop search cleanly"
  let some fallback ← searchWith { maxJevCalls := 0, maxDepth := 2, maxCost := 2 }
      controlledActions reverseRanker
    | throwError "deterministic fallback found no path"
  unless fallback.path.map (·.text) == ["intro jev_h", "exact jev_h"] do
    throwError "rank-call exhaustion did not retain catalogue order"
  evalTactic (← `(tactic| intro h; exact h))

/-- Failed candidates leave the original proof state available to later candidates. -/
example (P : Prop) (h : P) : P := by
  jev_test_restoration

/-- Intro, constructor, and exact form a genuine four-step path with sibling goals. -/
example (P : Prop) : P → P ∧ P := by
  jev_test_structural_path

/-- A successful structural transition is retained even though it leaves a goal. -/
example (P : Prop) : P → P := by
  jev_test_nonclosing_retained

/-- Exhausted node and rank-call budgets fail without mutating the caller state. -/
example (P : Prop) : P → P := by
  jev_test_budget_exhaustion

/-- Induction branches complete as an ordinary replayable tactic script. -/
example (xs : List Nat) : JevLean.tally xs = xs.length := by
  induction xs <;> (simp [JevLean.tally, *] <;> omega)

/-- Locally generated apply can open a successor that a later exact closes. -/
example (P Q : Prop) (h : P → Q) (hp : P) : Q := by
  jev_test_apply_path

/-- Tests can force aesop first and do not assume a structural action wins ranking. -/
example (P : Prop) : P → P := by
  jev_test_aesop_rank_seam

/-- A closing path must solve every outstanding goal, not just the focused one. -/
example (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  constructor
  jev?

/-- The raw multi-line suggestion source replays as ordinary tactics. -/
example (P : Prop) : P → P ∧ P := by
  intro h
  constructor
  exact h
  exact h

/-- Bounded aesop configuration remains valid ordinary tactic source. -/
example (P Q : Prop) : P ∧ Q → Q ∧ P := by
  aesop (config := { terminal := true, maxRuleApplications := 32 })
