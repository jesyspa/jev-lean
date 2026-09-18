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
  cost : Nat := 1

/-- A restorable search node. Goals retain Lean's active-goal order. -/
structure Node where
  state : Lean.Elab.Tactic.SavedState
  goals : List MVarId
  path : List Action
  depth : Nat
  cost : Nat

/-- Deterministic theorem-wide limits for a single search invocation.
`maxHeartbeats` counts attempted catalogue transitions. -/
structure Config where
  maxDepth : Nat := 6
  maxCost : Nat := 6
  maxNodes : Nat := 64
  maxHeartbeats : Nat := 256
  maxJevCalls : Nat := 16
  maxWallMs : Nat := 2_000

/-- The concrete state supplied to a ranker without exposing mutable tactic state. -/
structure RankContext where
  focusedGoal : String
  pendingGoals : List String
  path : List String
  remainingWallMs : Nat := 0

/-- A seam used by tests and alternative bounded action generators. -/
abbrev ActionSource := TacticM (List Action)

/-- A seam used to replace the external ranker while retaining the same scheduler. -/
abbrev ActionRanker := RankContext → List Action → TacticM (List Action)

private partial def freshIntroName (used : List Name) (index : Nat := 0) : Name :=
  let candidate := Name.mkSimple <| if index == 0 then "jev_h" else s!"jev_h{index}"
  if used.contains candidate then freshIntroName used (index + 1) else candidate

private def structuralActions : TacticM (List Action) := do
  let lctx ← getLCtx
  let used := lctx.foldl (init := []) fun names decl => decl.userName :: names
  let introName := freshIntroName used
  let ident := mkIdent introName
  let mut actions := [
    { tacticSyntax := ← `(tactic| intro $(ident):ident), text := s!"intro {introName}" },
    { tacticSyntax := ← `(tactic| constructor), text := "constructor" },
    { tacticSyntax := ← `(tactic| left), text := "left" },
    { tacticSyntax := ← `(tactic| right), text := "right" }
  ]
  for decl in lctx do
    if !decl.isImplementationDetail && !decl.userName.isAnonymous then
      let typ ← inferType decl.toExpr
      let ident := mkIdent decl.userName
      if ← isProp typ then
        actions := actions.concat { tacticSyntax := ← `(tactic| cases $ident:ident), text := s!"cases {decl.userName}" }
      else
        actions := actions.concat { tacticSyntax := ← `(tactic| induction $ident:ident), text := s!"induction {decl.userName}" }
  pure actions

private def closingActions : TacticM (List Action) := do
  pure [
    { tacticSyntax := ← `(tactic| rfl), text := "rfl" },
    { tacticSyntax := ← `(tactic| assumption), text := "assumption" },
    { tacticSyntax := ← `(tactic| simp), text := "simp" },
    { tacticSyntax := ← `(tactic| omega), text := "omega" },
    { tacticSyntax := ← `(tactic| norm_num), text := "norm_num" },
    { tacticSyntax := ← `(tactic| aesop (config := { terminal := true, maxRuleApplications := 32 })),
      text := "aesop (config := { terminal := true, maxRuleApplications := 32 })" }
  ]

private def localActions : TacticM (List Action) := do
  let lctx ← getLCtx
  lctx.foldlM (init := []) fun actions decl => do
    if decl.isImplementationDetail || decl.userName.isAnonymous then
      pure actions
    else
      let ident := mkIdent decl.userName
      pure (actions ++ [
        { tacticSyntax := ← `(tactic| exact $ident), text := s!"exact {decl.userName}" },
        { tacticSyntax := ← `(tactic| apply $ident), text := s!"apply {decl.userName}" }
      ])

/-- Build the finite, syntax-safe action catalogue for the current active goal. -/
def catalogue : TacticM (List Action) := do
  return (← structuralActions) ++ (← localActions) ++ (← closingActions)

/-- Ask the external ranker to order fixed catalogue entries, retaining local order on failure. -/
def rank (context : RankContext) (actions : List Action) : TacticM (List Action) := do
  let entries := actions.zipIdx.map fun (action, index) =>
    Json.mkObj [("id", Json.str s!"A{index + 1}"), ("tactic", Json.str action.text)]
  let request := Json.mkObj [
    ("focused_goal", Json.str context.focusedGoal),
    ("pending_sibling_goals", Json.arr (context.pendingGoals.map Json.str).toArray),
    ("path", Json.arr (context.path.map Json.str).toArray),
    ("actions", Json.arr entries.toArray)
  ]
  try
    let output ← IO.Process.output {
      cmd := "python3"
      args := #["-m", "jevlean.rank", "--plain", "--timeout-ms", toString context.remainingWallMs]
    } (some request.compress)
    if output.exitCode != 0 then return actions
    let indices := output.stdout.splitOn "\n" |>.filterMap fun line =>
      if line.startsWith "A" then (line.drop 1).toNat? else none
    if indices.length != actions.length || indices.eraseDups.length != actions.length ||
        indices.any fun index => index == 0 || index > actions.length then
      return actions
    let ranked := indices.filterMap fun index => actions[index - 1]?
    if ranked.length != actions.length then return actions
    return ranked
  catch _ => return actions

/-- Capture the current tactic state as the root of a search. -/
def initialNode : TacticM Node := do
  let goals ← getUnsolvedGoals
  return { state := ← saveState, goals, path := [], depth := 0, cost := 0 }

/-- Restore both Lean's metavariable state and the node's ordered active goals. -/
def Node.restore (node : Node) : TacticM Unit := do
  node.state.restore
  setGoals node.goals

private def expandUpTo (node : Node) (actions : List Action) (maxAttempts : Nat)
    (deadline? : Option Nat := none) : TacticM (List Node × Nat) := do
  let originalGoals ← getGoals
  let original ← saveState
  let mut successors : List Node := []
  let mut attempts := 0
  try
    for action in actions.take maxAttempts do
      let now ← IO.monoMsNow
      if deadline?.any fun deadline => now >= deadline then
        pure ()
      else
        attempts := attempts + 1
        node.restore
        match node.goals with
        | [] => pure ()
        | goal :: siblings =>
          setGoals [goal]
          try
            withMainContext do
              Term.withoutErrToSorry <| withoutRecover do evalTactic action.tacticSyntax
            let descendants ← getUnsolvedGoals
            setGoals (descendants ++ siblings)
            let goals ← getUnsolvedGoals
            successors := successors.concat {
              state := ← saveState
              goals
              path := node.path.concat action
              depth := node.depth + 1
              cost := node.cost + action.cost
            }
          catch _ => pure ()
    return (successors, attempts)
  finally
    original.restore
    setGoals originalGoals

/-- Try every action on only the first active goal, then reattach untouched siblings. -/
def expand (node : Node) (actions : List Action) : TacticM (List Node) := do
  return (← expandUpTo node actions actions.length).1

/-- Render a node's focused goal, ordered siblings, and preceding actions for ranking. -/
def rankContext (node : Node) : TacticM RankContext := do
  match node.goals with
  | [] => return { focusedGoal := "no goals", pendingGoals := [], path := node.path.map (·.text) }
  | goal :: siblings =>
    return {
      focusedGoal := (← ppGoal goal).pretty
      pendingGoals := ← siblings.mapM fun sibling => return (← ppGoal sibling).pretty
      path := node.path.map (·.text)
    }

/-- Deterministic FIFO frontier search. Every configured budget spans the whole invocation. -/
def searchWith (config : Config) (source : ActionSource) (ranker : ActionRanker) : TacticM (Option Node) := do
  let originalGoals ← getGoals
  let original ← saveState
  let root ← initialNode
  let start ← IO.monoMsNow
  let rec visit (frontier : List Node) (nodeFuel attempts calls : Nat) : TacticM (Option Node) := do
    match nodeFuel, frontier with
    | _, [] | 0, _ => return none
    | nodeFuel + 1, node :: rest =>
      let elapsed ← IO.monoMsNow
      if elapsed >= start + config.maxWallMs then return none
      if node.goals.isEmpty then return some node
      if node.depth >= config.maxDepth || node.cost >= config.maxCost then
        visit rest nodeFuel attempts calls
      else if attempts >= config.maxHeartbeats then
        return none
      else
        node.restore
        let (actions, calls) ← withMainContext do
          let actions ← source
          if calls >= config.maxJevCalls then
            pure (actions, calls)
          else
            let context ← rankContext node
            let now ← IO.monoMsNow
            let context := { context with remainingWallMs := start + config.maxWallMs - now }
            return (← ranker context actions, calls + 1)
        let remainingAttempts := config.maxHeartbeats - attempts
        let deadline := start + config.maxWallMs
        let (successors, usedAttempts) ← expandUpTo node actions remainingAttempts (some deadline)
        let successors := successors.filter fun successor =>
          successor.depth <= config.maxDepth && successor.cost <= config.maxCost
        if let some closed := successors.find? fun successor => successor.goals.isEmpty then
          return some closed
        visit (rest ++ successors) nodeFuel (attempts + usedAttempts) calls
  try
    visit [root] config.maxNodes 0 0
  finally
    original.restore
    setGoals originalGoals

/-- Replay a path as ordinary tactic source on Lean's current ordered goals. -/
def replay (path : List Action) : TacticM Unit := do
  for action in path do
    evalTactic action.tacticSyntax

/-- Search ordinary locally generated actions and provide a replayable replacement. -/
elab "jev?" : tactic => withMainContext do
  match ← searchWith {} catalogue rank with
  | none => throwError "jev? found no closing path in its bounded catalogue"
  | some node =>
    replay node.path
    Lean.Meta.Tactic.TryThis.addSuggestion (← getRef)
      { suggestion := .string (String.intercalate "\n" (node.path.map (·.text))) }

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
