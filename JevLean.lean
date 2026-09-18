/-
# Jev-first proof-search experiment

The executable experiment lives in `jevlean/`. This module pins and checks the
Lean/Mathlib environment used to verify candidate actions.
-/

import Aesop
import Mathlib.Tactic.TryThis
import Std.Internal.Async.TCP
import Std.Internal.Async.Timer

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
  maxWallMs : Nat := 10_000
  maxRetrievedNames : Nat := 24

/-- The concrete state supplied to a ranker without exposing mutable tactic state. -/
structure RankContext where
  focusedGoal : String
  pendingGoals : List String
  path : List String
  remainingWallMs : Nat := 0

/-- A seam used by tests and alternative bounded action generators. -/
abbrev ActionSource := Config → TacticM (List Action)

/-- A seam used to replace the external ranker while retaining the same scheduler. -/
abbrev ActionRanker := RankContext → List Action → TacticM (List Action)

private partial def freshIntroName (used : List Name) (index : Nat := 0) : Name :=
  let candidate := Name.mkSimple <| if index == 0 then "jev_h" else s!"jev_h{index}"
  if used.contains candidate then freshIntroName used (index + 1) else candidate

/-- Locals introduced by replayed tactics have fresh macro scopes and cannot be named in later syntax. -/
private def hasReplayableUserName (decl : LocalDecl) : Bool :=
  !decl.isImplementationDetail && !decl.userName.isAnonymous && !decl.userName.hasMacroScopes

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
    if hasReplayableUserName decl then
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
    { tacticSyntax := ← `(tactic| aesop (config := { terminal := true, maxRuleApplications := 32 })),
      text := "aesop (config := { terminal := true, maxRuleApplications := 32 })" }
  ]

private def localActions : TacticM (List Action) := do
  let lctx ← getLCtx
  lctx.foldlM (init := []) fun actions decl => do
    if hasReplayableUserName decl then
      let ident := mkIdent decl.userName
      pure (actions ++ [
        { tacticSyntax := ← `(tactic| exact $ident), text := s!"exact {decl.userName}" },
        { tacticSyntax := ← `(tactic| apply $ident), text := s!"apply {decl.userName}" }
      ])
    else
      pure actions

private partial def constantsIn (expr : Expr) (constants : List Name := []) : List Name :=
  match expr with
  | .const name _ => if constants.contains name then constants else name :: constants
  | .app function argument => constantsIn argument (constantsIn function constants)
  | .lam _ type body _ | .forallE _ type body _ => constantsIn body (constantsIn type constants)
  | .letE _ type value body _ => constantsIn body (constantsIn value (constantsIn type constants))
  | .mdata _ body | .proj _ _ body => constantsIn body constants
  | _ => constants

private partial def forallBody : Expr → Expr
  | .forallE _ _ body _ => forallBody body
  | expr => expr

private def retrievalScore (query : List Name) (type : Expr) : Nat :=
  (constantsIn (forallBody type)).countP query.contains

private def candidateWorks (action : Action) : TacticM Bool := do
  let goals ← getGoals
  let state ← saveState
  try
    withMainContext do
      Term.withoutErrToSorry <| withoutRecover do evalTactic action.tacticSyntax
    return true
  catch _ => return false
  finally
    state.restore
    setGoals goals

/-- Retrieve globally named declarations related to the focused goal, then retain only candidates
that Lean can elaborate and execute in the current tactic state. -/
def globalActions (maxNames : Nat) : TacticM (List Action) := do
  let goal ← getMainGoal
  let target ← goal.getType
  let lctx ← getLCtx
  let query := lctx.foldl (init := constantsIn target) fun names decl =>
    constantsIn decl.type names
  let names ← (← getEnv).constants.map₂.foldlM (init := []) fun names name _ => do
    let some info := (← getEnv).find? name | return names
    let score := retrievalScore query info.type
    if score == 0 then return names
    return (score, name) :: names
  let names := names.mergeSort fun left right =>
    left.1 > right.1 || left.1 == right.1 && left.2.toString < right.2.toString
  let mut actions := []
  for (_, name) in names.take maxNames do
    let ident := mkIdent name
    let exactAction : Action := {
      tacticSyntax := ← `(tactic| exact $ident), text := s!"exact {name}"
    }
    if ← candidateWorks exactAction then actions := actions.concat exactAction
    let applyAction : Action := {
      tacticSyntax := ← `(tactic| apply $ident), text := s!"apply {name}"
    }
    if ← candidateWorks applyAction then actions := actions.concat applyAction
  return actions

/-- Build the finite, syntax-safe action catalogue for the current active goal. -/
def catalogue (config : Config) : TacticM (List Action) := do
  return (← structuralActions) ++ (← localActions) ++
    (← globalActions config.maxRetrievedNames) ++ (← closingActions)

private initialize rankCache : IO.Ref (Std.HashMap String (List Nat)) ← IO.mkRef {}

private def applyRanking? (actions : List Action) (indices : List Nat) : Option (List Action) := do
  if indices.length != actions.length || indices.eraseDups.length != actions.length ||
      indices.any fun index => index == 0 || index > actions.length then
    none
  let ranked := indices.filterMap fun index => actions[index - 1]?
  if ranked.length == actions.length then some ranked else none

private def brokerAddress : IO Std.Net.SocketAddress := do
  let port := match (← IO.getEnv "JEV_RANK_BROKER_PORT").bind String.toNat? with
    | some port => port
    | none => 8765
  if port == 0 || port > 65535 then
    throw <| IO.Error.userError "JEV_RANK_BROKER_PORT must be between 1 and 65535"
  return .v4 { addr := Std.Net.IPv4Addr.ofParts 127 0 0 1, port := port.toUInt16 }

private def beforeDeadline (operation : Std.Internal.IO.Async.Async α) (deadline : Nat) : IO α := do
  let now ← IO.monoMsNow
  if now >= deadline then
    throw <| IO.Error.userError "rank broker request deadline exceeded"
  let delay := Std.Time.Millisecond.Offset.ofNat (deadline - now)
  let result ← Std.Internal.IO.Async.Async.race (some <$> operation)
    (Std.Internal.IO.Async.sleep delay *> pure none) |>.block
  let some result := result | throw <| IO.Error.userError "rank broker request deadline exceeded"
  return result

private partial def receiveBrokerFrame (socket : Std.Internal.IO.Async.TCP.Socket.Client)
    (deadline : Nat) (received : ByteArray := ByteArray.empty) : IO String := do
  if received.size > 1_000_000 then
    throw <| IO.Error.userError "rank broker response exceeds 1000000 bytes"
  let some chunk ← beforeDeadline (socket.recv? 65536) deadline |
    throw <| IO.Error.userError "rank broker closed the response"
  let received := received ++ chunk
  if received.toList.contains '\n'.toUInt8 then
    let some text := String.fromUTF8? received | throw <| IO.Error.userError "rank broker response is not UTF-8"
    return (text.takeWhile (· != '\n')).toString
  receiveBrokerFrame socket deadline received

private def brokerRanking (request : Json) (deadline : Nat) : IO (List Nat) := do
  let socket ← Std.Internal.IO.Async.TCP.Socket.Client.mk
  beforeDeadline (socket.connect (← brokerAddress)) deadline
  beforeDeadline (socket.send ((Json.mkObj [
    ("request", request), ("deadline_ms", Json.num (deadline - (← IO.monoMsNow)))
  ]).compress.toUTF8 ++ "\n".toUTF8)) deadline
  let response ← receiveBrokerFrame socket deadline
  let json ← match Json.parse response with
    | .ok json => pure json
    | .error error => throw <| IO.Error.userError s!"rank broker response is invalid JSON: {error}"
  let object ← match json.getObj? with
    | .ok object => pure object
    | .error error => throw <| IO.Error.userError s!"rank broker response is not an object: {error}"
  let some okJson := object.get? "ok" | throw <| IO.Error.userError "rank broker response has no ok field"
  let ok ← match okJson.getBool? with
    | .ok ok => pure ok
    | .error error => throw <| IO.Error.userError s!"rank broker response has invalid ok field: {error}"
  unless ok do
    let error := ((object.get? "error").bind fun value => value.getStr?.toOption).getD "unknown error"
    throw <| IO.Error.userError s!"rank broker rejected request: {error}"
  let some rankingJson := object.get? "ranking" | throw <| IO.Error.userError "rank broker response has no ranking field"
  let ranking ← match rankingJson.getArr? with
    | .ok ranking => pure ranking
    | .error error => throw <| IO.Error.userError s!"rank broker response has invalid ranking field: {error}"
  ranking.toList.mapM fun identifier => do
    let identifier ← match identifier.getStr? with
      | .ok identifier => pure identifier
      | .error error => throw <| IO.Error.userError s!"rank broker returned non-string identifier: {error}"
    let some index := if identifier.startsWith "A" then (identifier.drop 1).toNat? else none |
      throw <| IO.Error.userError "rank broker returned invalid action identifier"
    return index

/-- Ask the persistent localhost rank broker to order fixed catalogue entries.
Successful rankings are cached for the lifetime of the Lean process so incremental re-elaboration
of an unchanged proof state does not repeat the external call. -/
def rank (context : RankContext) (actions : List Action) : TacticM (List Action) := do
  let entries := actions.zipIdx.map fun (action, index) =>
    Json.mkObj [("id", Json.str s!"A{index + 1}"), ("tactic", Json.str action.text)]
  let request := Json.mkObj [
    ("focused_goal", Json.str context.focusedGoal),
    ("pending_sibling_goals", Json.arr (context.pendingGoals.map Json.str).toArray),
    ("path", Json.arr (context.path.map Json.str).toArray),
    ("actions", Json.arr entries.toArray)
  ]
  let requestText := request.compress
  if let some indices := (← rankCache.get).get? requestText then
    if let some ranked := applyRanking? actions indices then
      return ranked
  let deadline ← IO.monoMsNow.map (· + context.remainingWallMs)
  let indices ← try brokerRanking request deadline catch error =>
    throwError "jev? rank broker is unavailable or failed: {error.toMessageData}"
  let some ranked := applyRanking? actions indices |
    throwError "jev? rank broker returned an invalid action ordering"
  rankCache.modify fun cache =>
    let cache := if cache.size >= 1024 then {} else cache
    cache.insert requestText indices
  return ranked

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

/-- Deterministic rank-guided depth-first search. Every configured budget spans the whole invocation. -/
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
          let actions ← source config
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
        visit (successors ++ rest) nodeFuel (attempts + usedAttempts) calls
  try
    visit [root] config.maxNodes 0 0
  finally
    original.restore
    setGoals originalGoals

/-- Replay a path as ordinary tactic source on Lean's current ordered goals. -/
def replay (path : List Action) : TacticM Unit := do
  for action in path do
    evalTactic action.tacticSyntax

private abbrev ProofLine := Nat × String

private partial def renderGoal (depth : Nat) (steps : List (Action × Nat)) :
    TacticM (List ProofLine × List (Action × Nat)) := do
  let (action, childCount) :: rest := steps |
    throwError "cannot format an incomplete Jev proof path"
  if childCount == 0 then
    return ([(depth, action.text)], rest)
  if childCount == 1 then
    let (child, rest) ← renderGoal depth rest
    return ((depth, action.text) :: child, rest)
  let mut rest := rest
  let mut lines : List ProofLine := [(depth, action.text)]
  for _ in [:childCount] do
    let (child, remaining) ← renderGoal (depth + 1) rest
    let (_, first) :: tail := child |
      throwError "cannot format an empty Jev proof branch"
    lines := lines ++ (depth, s!"· {first}") :: tail
    rest := remaining
  return (lines, rest)

private def renderForest (rootCount indent : Nat) (steps : List (Action × Nat)) :
    TacticM String := do
  let mut rest := steps
  let mut lines : List ProofLine := []
  for _ in [:rootCount] do
    let (root, remaining) ← renderGoal (if rootCount == 1 then 0 else 1) rest
    if rootCount == 1 then
      lines := root
    else
      let (_, first) :: tail := root |
        throwError "cannot format an empty Jev proof root"
      lines := lines ++ (0, s!"· {first}") :: tail
    rest := remaining
  unless rest.isEmpty do
    throwError "cannot format a Jev proof path with unused steps"
  let some (_, first) := lines.head? |
    throwError "cannot format an empty Jev proof path"
  return lines.tail.foldl (init := first) fun text line =>
    text ++ "\n" ++ String.ofList (List.replicate (indent + 2 * line.1) ' ') ++ line.2

/-- Replay a closing path and format its branching structure as an indented tactic sequence. -/
def replaySuggestion (path : List Action) (indent : Nat := 0) : TacticM String := do
  let rootCount := (← getUnsolvedGoals).length
  let mut steps : List (Action × Nat) := []
  for action in path do
    let goalsBefore ← getUnsolvedGoals
    let siblingCount := goalsBefore.length - 1
    evalTactic action.tacticSyntax
    let goalsAfter ← getUnsolvedGoals
    if goalsAfter.length < siblingCount then
      throwError "a Jev replay action changed an untouched sibling goal"
    steps := steps.concat (action, goalsAfter.length - siblingCount)
  renderForest rootCount indent steps

/-- Search ordinary locally generated actions and provide a replayable replacement. -/
elab "jev?" : tactic => withMainContext do
  match ← searchWith {} catalogue rank with
  | none => throwError "jev? found no closing path in its bounded catalogue"
  | some node =>
    let ref ← getRef
    let some range := ref.getRange? |
      throwError "jev? cannot format a suggestion without a source range"
    let (indent, _) := Lean.Meta.Tactic.TryThis.getIndentAndColumn (← getFileMap) range
    let suggestion ← replaySuggestion node.path indent
    Lean.Meta.Tactic.TryThis.addSuggestion ref { suggestion }

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
