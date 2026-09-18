import JevLean

open Lean Elab Tactic Meta
open JevLean.Search

private def controlledActions : ActionSource := fun _ => do
  return (← catalogue { maxRetrievedNames := 0 }).filter fun action =>
    action.text.startsWith "intro " || action.text == "constructor" ||
      action.text.startsWith "exact " || action.text.startsWith "apply "

private def identityRanker : ActionRanker := fun _ actions => pure actions

private def retrievedActions : ActionSource := fun config =>
  return (← globalActions config.maxRetrievedNames).filter fun action =>
    action.text.startsWith "exact " || action.text.startsWith "apply "

private def retrievalThenLocalActions : ActionSource := fun config => do
  let retrieved ← globalActions config.maxRetrievedNames
  let h := mkIdent `h
  return retrieved.filter (·.text == "apply sipserAcceptance_from") ++ [
    { tacticSyntax := ← `(tactic| exact $h), text := "exact h" }
  ]

def SipserAcceptance (n : Nat) : Prop := n = n

lemma sipserAcceptance_global (n : Nat) : SipserAcceptance n := rfl

lemma sipserAcceptance_zero : SipserAcceptance 0 := rfl

lemma sipserAcceptance_from (n : Nat) (h : n = 0) : SipserAcceptance n := by
  subst n
  rfl

def SipserAccepted (n : Nat) : Prop := n = 0

def SipserClosure (p : Prop) : Prop := p ∧ True

@[simp] lemma sipserClosure_normalize (p : Prop) : SipserClosure p = p := by
  simp [SipserClosure]

private def rewriteThenCloseActions : ActionSource := fun config => do
  return (← rewriteActions config) ++ [
    { tacticSyntax := ← `(tactic| rfl), text := "rfl" }
  ]

private def aesopFirst : ActionRanker := fun _ actions =>
  pure <| actions.mergeSort fun left right =>
    left.text.startsWith "aesop" && !right.text.startsWith "aesop"

private def reverseRanker : ActionRanker := fun _ actions => pure actions.reverse

private def disjunctionActions : ActionSource := fun _ => do
  return (← catalogue { maxRetrievedNames := 0 }).filter fun action =>
    action.text.startsWith "intro " || action.text.startsWith "cases " ||
      action.text == "left" || action.text == "right" || action.text == "assumption"

private def inductionActions : ActionSource := fun _ => do
  let xs := mkIdent `xs
  return [
    { tacticSyntax := ← `(tactic| induction $xs:ident), text := "induction xs" },
    { tacticSyntax := ← `(tactic| simp [JevLean.tally, *]), text := "simp [JevLean.tally, *]" },
    { tacticSyntax := ← `(tactic| omega), text := "omega" }
  ]

private def orderedSiblingActions : ActionSource := fun _ => do
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

elab "jev_test_global_retrieval" : tactic => withMainContext do
  let config := { maxRetrievedNames := 24 }
  let actions ← retrievedActions config
  let repeated ← retrievedActions config
  unless actions.map (·.text) == repeated.map (·.text) && actions.length <= 2 * config.maxRetrievedNames do
    throwError "global retrieval exceeded its bound or changed order"
  let root ← initialNode
  unless (← expand root actions).length == actions.length do
    throwError "global retrieval admitted a candidate that does not elaborate in the focused state"
  unless actions.any fun action => action.text == "exact sipserAcceptance_zero" do
    throwError "type-correct global exact candidate was not retrieved"
  unless actions.all fun action => action.text != "exact definitely_missing_lemma" do
    throwError "retrieval invented an unavailable name"
  let some node ← searchWith { maxDepth := 1, maxCost := 1, maxRetrievedNames := 24 }
      retrievedActions identityRanker
    | throwError "retrieval search found no path"
  unless node.path.map (·.text) == ["exact sipserAcceptance_zero"] do
    throwError "retrieval did not select the global exact candidate"
  replay node.path

elab "jev_test_global_apply_retrieval" : tactic => withMainContext do
  let actions ← retrievedActions { maxRetrievedNames := 24 }
  unless actions.any fun action => action.text == "apply sipserAcceptance_from" do
    throwError "type-correct global apply candidate was not retrieved"
  let some node ← searchWith { maxDepth := 2, maxCost := 2, maxRetrievedNames := 24 }
      retrievalThenLocalActions identityRanker
    | throwError "retrieval apply search found no path"
  unless node.path.map (·.text) == ["apply sipserAcceptance_from", "exact h"] do
    throwError "retrieval did not retain a type-correct global apply candidate"
  replay node.path

elab "jev_test_rewrite_acceptance" : tactic => withMainContext do
  let config := {
    maxDepth := 2, maxCost := 2, maxRewriteCandidates := 2,
    maxRewriteSimpNames := 100_000, maxRewriteMs := 10_000
  }
  let actions ← rewriteActions config
  unless actions.map (·.text) == ["rw [h]", "rw [← h]"] do
    throwError "acceptance equality did not produce deterministic rewrites: {actions.map (·.text)}"
  let some node ← searchWith config rewriteThenCloseActions identityRanker
    | throwError "acceptance rewrite search found no path"
  unless node.path.map (·.text) == ["rw [h]"] do
    throwError "acceptance rewrite path is not replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_rewrite_closure" : tactic => withMainContext do
  let config := {
    maxDepth := 3, maxCost := 3, maxWallMs := 120_000, maxRewriteCandidates := 12,
    maxRewriteSimpLemmas := 4, maxRewriteSimpNames := 100_000, maxRewriteMs := 120_000
  }
  let actions ← rewriteActions config
  unless actions.any fun action => action.text == "rw [h]" do
    throwError "closure equality did not produce a forward rewrite"
  unless actions.any fun action => action.text == "simp only [sipserClosure_normalize]" do
    throwError "closure normalization did not produce a matched simp-only action: {actions.map (·.text)}"
  let some node ← searchWith config rewriteThenCloseActions identityRanker
    | throwError "closure rewrite search found no path"
  unless node.path.map (·.text) ==
      ["rw [h]", "simp only [sipserClosure_normalize]"] do
    throwError "closure normalization path is not replayable: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_aesop_rank_seam" : tactic => withMainContext do
  let some node ← searchWith { maxDepth := 2, maxCost := 2 } catalogue aesopFirst
    | throwError "aesop-first search found no path"
  unless node.path.head?.map (·.text.startsWith "aesop") == some true do
    throwError "injected rank order was not respected"
  replay node.path

elab "jev_test_custom_disjunction" : tactic => withMainContext do
  let some node ← searchWith {} disjunctionActions identityRanker
    | throwError "custom disjunction search found no path"
  unless node.path.map (·.text) ==
      ["intro jev_h", "cases jev_h", "right", "assumption", "left", "assumption"] do
    throwError "unexpected custom disjunction path: {node.path.map (·.text)}"
  replay node.path

elab "jev_test_nonclosing_retained" : tactic => withMainContext do
  let root ← initialNode
  let successors ← expand root (← controlledActions {})
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
  let suggestion ← replaySuggestion node.path 2
  unless suggestion ==
      "induction xs\n  · simp [JevLean.tally, *]\n  · simp [JevLean.tally, *]\n    omega" do
    throwError "unexpected formatted suggestion:\n{suggestion}"

/-- Failed candidates leave the original proof state available to later candidates. -/
example (P : Prop) (h : P) : P := by
  jev_test_restoration

/-- Intro, constructor, and exact form a genuine four-step path with sibling goals. -/
example (P : Prop) : P → P ∧ P := by
  jev_test_structural_path

private inductive TestDisj (P Q : Prop) : Prop where
  | inl (p : P)
  | inr (q : Q)

/-- Constructor case binders replay through stable tactics. -/
example : TestDisj P Q → TestDisj Q P := by
  jev_test_custom_disjunction

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

/-- A Sipser-shaped target is solved by a named global lemma absent from the local catalogue. -/
example : SipserAcceptance 0 := by
  jev_test_global_retrieval

/-- Retrieved global applications open ordinary local proof obligations. -/
example (n : Nat) (h : n = 0) : SipserAcceptance n := by
  jev_test_global_apply_retrieval

/-- Generated equality rewrites solve the acceptance shape and replay from the root state. -/
example (n : Nat) (h : n = 0) : SipserAccepted n = SipserAccepted 0 := by
  jev_test_rewrite_acceptance

/-- Generated simp-only normalization follows an equality rewrite and replays from the root state. -/
example (n : Nat) (h : n = 0) : SipserClosure (n = 0) := by
  jev_test_rewrite_closure

/-- Tests can force aesop first and do not assume a structural action wins ranking. -/
example (P : Prop) : P → P := by
  jev_test_aesop_rank_seam

/-- The formatted multi-line suggestion replays with explicit branches. -/
example (P : Prop) : P → P ∧ P := by
  intro h
  constructor
  · exact h
  · exact h

/-- Nested continuation lines remain inside the appropriate induction branch. -/
example (xs : List Nat) : JevLean.tally xs = xs.length := by
  induction xs
  · simp [JevLean.tally]
  · simp [JevLean.tally, *]
    omega

/-- Bounded aesop configuration remains valid ordinary tactic source. -/
example (P Q : Prop) : P ∧ Q → Q ∧ P := by
  aesop (config := { terminal := true, maxRuleApplications := 32 })
