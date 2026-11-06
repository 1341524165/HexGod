# Selection recording repair plan — November 6

## Evidence and scope

The player reports three missing selections: Volibear first offer — 将他们放倒倒倒;
Twisted Fate first offer — 回归基本功; Twisted Fate second offer — 术士果汁盒.
The November 6 journals contain those titles. Rejected cursor samples were not
retained, so the exact historical cursor path cannot be reconstructed.
The fixtures preserve recorded card geometry and user-reported outcomes;
test cursor positions are explicitly synthetic. Historical journals stay unchanged.

## Implementation steps

1. Accept the full detected card rectangle. The previous four-percent horizontal
   and three-percent vertical inset excludes visible card edges. Reroll buttons
   remain separate and are rejected explicitly.
2. Retain recent cursor samples throughout foreground capture, including points
   outside cards. Resolve the latest sample at or before disappearance against
   the last visible offer. Reject a sample when its own slot has since changed.
   This avoids falling back to an older hovered card after visiting a reroll.
3. Preserve a candidate when an unrelated offer replaces the previous one before
   its five-second delay expires. Cancel it if the same offer reappears. Continue
   tracking the new offer and keep it visible when the older result is applied.
   Different-offer detection requires three known slot names with no positional
   overlap; ambiguous partial reads do not establish a new round.
4. Map the exact OCR title 喂鸣喂鸣 to catalog title 喂呜喂呜. Keep the existing
   conservative fuzzy threshold for other titles and reject description substrings.
5. Log candidate counts, cursor coordinates, timing and rejection reason, including
   unresolved selections. Preserve the distinction between inferred and confirmed.

## Validation

Exercise the reported geometry at card edges, rapid consecutive offers, reopens,
rerolls, stale frames, other windows, five-second timing and the full OCR-to-catalog
alias path. Test the C# renderer and compile the root executable. Run the repository
verification script and report existing data-version failures separately.

These checks establish implementation behavior, not actual-game selection accuracy.
The five-second hover method remains a heuristic: hiding an offer can temporarily
produce an inference, and a missing cursor sample cannot establish the player's pick.

Completed validation: 151 Python tests, legacy and current-data WPF smoke tests,
and the verification build pass. Exact statistical regression tests now explicitly
use their original 16.19.1 fixture; production rendering assertions use the active
data's names and sample counts. The release build refreshed the root executable.
