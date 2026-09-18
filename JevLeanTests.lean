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

private def inductionActions : ActionSource := do
  let xs := mkIdent `xs
  return [
    { tacticSyntax := ← `(tactic| induction $xs:ident), text := "induction xs" },
    { tacticSyntax := ← `(tactic| simp [JevLean.tally, *]), text := "simp [JevLean.tally, *]" },
    { tacticSyntax := ← `(tactic| omega), text := "omega" }
  ]

private def orderedSiblingActions : ActionSource := do
  let hP := mkIdent `hP
  let hQ := mkIdent `hQ
  return [
    { tacticSyntax := ← `(tactic| constructor), text := "constructor" },
    { tacticSyntax := ← `(tactic| exact $hP), text := "exact hP" },
    { tacticSyntax := ← `(tactic| exact $hQ), text := "exact hQ" }
  ]

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
  unless (← searchWith { maxNodes := 1 } controlledActions identityRanker).isNone do
    throwError "the theorem-wide node budget was reset between branches"
  unless (← searchWith { maxHeartbeats := 1 } controlledActions identityRanker).isNone do
    throwError "the theorem-wide transition budget was not enforced"
  unless (← searchWith { maxWallMs := 0 } controlledActions identityRanker).isNone do
    throwError "the theorem-wide wall budget was not enforced"
  let some fallback ← searchWith { maxJevCalls := 0, maxDepth := 2, maxCost := 2 }
      controlledActions reverseRanker
    | throwError "deterministic fallback found no path"
  unless fallback.path.map (·.text) == ["intro jev_h", "exact jev_h"] do
    throwError "rank-call exhaustion did not retain catalogue order"
  evalTactic (← `(tactic| intro h; exact h))

elab "jev_test_failed_metavariable_branch" : tactic => withMainContext do
  let root ← initialNode
  let actions : List Action := [
    { tacticSyntax := ← `(tactic| refine ⟨(0 : Nat), ?_⟩ <;> fail), text := "failing refine" },
    { tacticSyntax := ← `(tactic| exact ⟨1, rfl⟩), text := "exact ⟨1, rfl⟩" }
  ]
  let successors ← expand root actions
  match successors with
  | [successor] =>
    unless successor.path.map (·.text) == ["exact ⟨1, rfl⟩"] do
      throwError "the successful branch path was displaced"
  | _ => throwError "a failed branch leaked or removed a successful sibling branch"
  evalTactic (← `(tactic| exact ⟨1, rfl⟩))

elab "jev_test_ordered_siblings" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 3, maxCost := 3 }
      orderedSiblingActions identityRanker
    | throwError "ordered sibling search found no path"
  unless node.path.map (·.text) == ["constructor", "exact hP", "exact hQ"] do
    throwError "sibling goal order changed: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_induction_replay" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 4, maxCost := 4 }
      inductionActions identityRanker
    | throwError "induction search found no path"
  unless node.path.map (·.text) ==
      ["induction xs", "simp [JevLean.tally, *]", "simp [JevLean.tally, *]", "omega"] do
    throwError "unexpected induction path: {node.path.map (·.text)}"
  replay node.path

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

/-- Failed refinements cannot leak assigned metavariables into later candidates. -/
example : ∃ n : Nat, n = 1 := by
  jev_test_failed_metavariable_branch

/-- Descendants stay ahead of untouched siblings and retain their local names. -/
example (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  jev_test_ordered_siblings

/-- Search-generated induction branches retain and replay their induction hypotheses. -/
example (xs : List Nat) : JevLean.tally xs = xs.length := by
  jev_test_induction_replay

/-- Locally generated apply can open a successor that a later exact closes. -/
example (P Q : Prop) (h : P → Q) (hp : P) : Q := by
  jev_test_apply_path

/-- Tests can force aesop first and do not assume a structural action wins ranking. -/
example (P : Prop) : P → P := by
  jev_test_aesop_rank_seam

/-- The production tactic closes from its locally generated fixed catalogue. -/
example (P : Prop) (hP : P) : P := by
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
