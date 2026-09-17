/-
# Jev-first proof-search experiment

The executable experiment lives in `jevlean/`. This module pins and checks the
Lean/Mathlib environment used to verify candidate actions.
-/

import Mathlib

namespace JevLean

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
