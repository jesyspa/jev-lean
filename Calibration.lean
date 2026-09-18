import JevLean

set_option linter.unusedTactic false

open Lean Elab Tactic Meta
open JevLean.Search

private def identityRanker : ActionRanker := fun _ actions => pure actions

private def calibrationActions : ActionSource := fun config => do
  return (← catalogue config).filter fun action =>
    action.text != "simp" && !action.text.startsWith "aesop" && action.text != "omega"

private def preWorkConfig : JevLean.Search.Config := {
  maxRetrievedNames := 0
  maxRewriteCandidates := 0
  maxUnfoldCandidates := 0
  maxDataCases := 0
  maxPropCases := 0
  maxInductions := 0
  maxGeneralizingInductions := 0
  maxWitnesses := 0
  maxDestructures := 0
  maxLocalApplications := 0
  maxTranspositions := 0
}

private def replayed (path : List Action) : TacticM Bool := do
  let saved ← saveState
  try
    replay path
    return (← getUnsolvedGoals).isEmpty
  catch _ => return false
  finally saved.restore

private def resultJson (name mode : String) (result : Option Node) (metrics : SearchMetrics) :
    TacticM String := do
  let (solved, replaySuccess) ← match result with
    | none => pure (false, false)
    | some node => pure (true, ← replayed node.path)
  return s!"{name}|{mode}|{solved}|{replaySuccess}|{metrics.jevCalls}|{metrics.expandedNodes}|{metrics.attemptedTransitions}|{metrics.admittedSuccessors}|{metrics.duplicateSuccessors}|{metrics.transpositionEntries}|{metrics.elapsedMs}"

elab "jev_calibration" name:ident : tactic => withMainContext do
  for (mode, config) in [("pre_work", preWorkConfig), ("accumulated", ({} : JevLean.Search.Config))] do
    let (result, metrics) ← searchWithMetrics config calibrationActions identityRanker
    logInfo ("JEV_CALIBRATION::" ++ (← resultJson name.getId.toString mode result metrics))

def SipserAcceptance (n : Nat) : Prop := n = n
lemma sipserAcceptance_zero : SipserAcceptance 0 := rfl

def SipserAccepted (n : Nat) : Prop := n = 0

def SipserWitness (xs : List Nat) : Prop := ∃ ys : List Nat, ys = xs

inductive SipserTree where
  | leaf
  | branch : SipserTree → SipserTree → SipserTree

def SipserTree.leaves : SipserTree → Nat
  | .leaf => 1
  | .branch left right => left.leaves + right.leaves

/-- Frozen local baseline. -/
example (P : Prop) (h : P) : P := by
  jev_calibration local_baseline
  exact h

/-- Frozen Sipser-shaped goal requiring bounded global retrieval. -/
example : SipserAcceptance 0 := by
  jev_calibration retrieval
  exact sipserAcceptance_zero

/-- Frozen equality-normalization goal requiring a generated rewrite. -/
example (n : Nat) (h : n = 0) : SipserAccepted n = SipserAccepted 0 := by
  jev_calibration rewrite
  subst n
  rfl

/-- Frozen existential goal requiring head unfolding followed by a witness. -/
example (xs : List Nat) : SipserWitness xs := by
  jev_calibration unfolding
  exact ⟨xs, rfl⟩

/-- Frozen local application goal. -/
example (P : Nat → Prop) (h : ∀ n, P n) (n : Nat) : P n := by
  jev_calibration local_application
  exact h n

/-- Frozen structural-destructuring goal over a Sipser-shaped tree fact. -/
example (P Q : Prop) (h : P ∧ Q) : Q := by
  jev_calibration structural
  exact h.2
