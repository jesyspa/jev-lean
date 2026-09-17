/-
# Jev-first proof-search experiment

The executable experiment lives in `jevlean/`. This module pins and checks the
Lean/Mathlib environment used to verify candidate actions.
-/

import Mathlib
import Mathlib.Tactic.TryThis

namespace JevLean

namespace Search

open Lean Elab Tactic Meta

/-- A concrete Lean command generated locally from the current proof state. -/
structure Action where
  tacticSyntax : TSyntax `tactic
  text : String

/-- The goals remaining after applying one action to a proof state. -/
structure Successor where
  action : Action
  goals : List MVarId

/-- All locally checked transitions considered for one proof state. -/
structure SearchResult where
  successors : List Successor

private def standardActions : TacticM (List Action) := do
  pure [
    { tacticSyntax := ← `(tactic| all_goals rfl), text := "all_goals rfl" },
    { tacticSyntax := ← `(tactic| all_goals assumption), text := "all_goals assumption" },
    { tacticSyntax := ← `(tactic| all_goals simp), text := "all_goals simp" },
    { tacticSyntax := ← `(tactic| all_goals omega), text := "all_goals omega" },
    { tacticSyntax := ← `(tactic| all_goals norm_num), text := "all_goals norm_num" },
    { tacticSyntax := ← `(tactic| all_goals aesop (config := { terminal := true, maxRuleApplications := 32 })),
      text := "all_goals aesop (config := { terminal := true, maxRuleApplications := 32 })" }
  ]

private def localExactActions : TacticM (List Action) := do
  let lctx ← getLCtx
  lctx.foldlM (init := []) fun actions decl => do
    if decl.isImplementationDetail || decl.userName.isAnonymous then
      pure actions
    else
      let ident := mkIdent decl.userName
      pure (actions.concat {
        tacticSyntax := ← `(tactic| all_goals exact $ident)
        text := s!"all_goals exact {decl.userName}"
      })

/-- Build the finite, syntax-safe action catalogue for the current tactic state. -/
def catalogue : TacticM (List Action) := do
  return (← localExactActions) ++ (← standardActions)

/-- Run each action from the same saved state and retain its successor goals. -/
def explore (actions : List Action) : TacticM SearchResult := do
  let initial ← saveState
  let mut successors := []
  for action in actions do
    restoreState initial
    try
      evalTactic action.tacticSyntax
      successors := successors.concat { action, goals := ← getUnsolvedGoals }
    catch _ => pure ()
  restoreState initial
  return { successors }

/-- Commit a transition only when it closes every goal from the original state. -/
def firstClosing (result : SearchResult) : Option Successor :=
  result.successors.find? fun successor => successor.goals.isEmpty

/-- Search ordinary locally generated actions and provide a replayable replacement. -/
elab "jev?" : tactic => withMainContext do
  let actions ← catalogue
  let result ← explore actions
  match firstClosing result with
  | none => throwError "jev? found no closing action in its bounded catalogue"
  | some successor =>
    evalTactic successor.action.tacticSyntax
    Lean.Meta.Tactic.TryThis.addSuggestion (← getRef)
      { suggestion := .tsyntax successor.action.tacticSyntax }

end Search

open Search


/-- A small recursive function used by the public synthetic benchmark. -/
def tally : List Nat → Nat
  | [] => 0
  | _ :: xs => 1 + tally xs

@[simp] lemma tally_nil : tally [] = 0 := rfl

@[simp] lemma tally_cons (x : Nat) (xs : List Nat) : tally (x :: xs) = 1 + tally xs := rfl

/-- Sum of squares, kept separate from `List.sum` to expose induction states. -/
def sumSq : List Nat → Nat
  | [] => 0
  | x :: xs => x * x + sumSq xs

/-- A tail-recursive sum used by accumulator-generalization cases. -/
def totalFrom : Nat → List Nat → Nat
  | acc, [] => acc
  | acc, x :: xs => totalFrom (acc + x) xs

/-- A small binary tree for structural action generation. -/
inductive Tree (α : Type) where
  | leaf : α → Tree α
  | fork : Tree α → Tree α → Tree α
  deriving DecidableEq, Repr

namespace Tree

/-- Number of leaves in a tree. -/
def leaves : Tree α → Nat
  | .leaf _ => 1
  | .fork l r => leaves l + leaves r

/-- Exchange children recursively. -/
def mirror : Tree α → Tree α
  | .leaf x => .leaf x
  | .fork l r => .fork (mirror r) (mirror l)

/-- Apply a function at every leaf. -/
def map (f : α → β) : Tree α → Tree β
  | .leaf x => .leaf (f x)
  | .fork l r => .fork (map f l) (map f r)

end Tree

end JevLean
