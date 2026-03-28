'''
Encapsulates game and round state information for the player.
'''
import os
import sys
from collections import namedtuple
from .actions import FoldAction, CallAction, CheckAction, RaiseAction, RedrawAction

GameState = namedtuple('GameState', ['bankroll', 'game_clock', 'round_num'])
TerminalState = namedtuple('TerminalState', ['deltas', 'previous_state'])

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    import config as _engine_config

    NUM_ROUNDS = _engine_config.NUM_ROUNDS
    STARTING_STACK = _engine_config.STARTING_STACK
    BIG_BLIND = _engine_config.BIG_BLIND
    SMALL_BLIND = _engine_config.SMALL_BLIND
except ImportError:
    NUM_ROUNDS = 1000
    STARTING_STACK = 400
    BIG_BLIND = 2
    SMALL_BLIND = 1


class RoundState(
    namedtuple(
        '_RoundState',
        ['button', 'street', 'pips', 'stacks', 'hands', 'board', 'redraws_used', 'previous_state'],
    )
):
    '''
    Encodes the game tree for one round of poker.
    '''

    def showdown(self):
        '''
        Compares the players' hands and computes payoffs.
        Skeleton bots do not evaluate hands locally.
        '''
        return TerminalState([0, 0], self)

    def _board_target_limit(self):
        if self.street < 3:
            return -1
        if self.street == 3:
            return 2
        if self.street == 4:
            return 3
        return 4

    def _is_valid_redraw_target(self, active, target_type, target_index):
        if self.redraws_used[active] or self.street >= 5:
            return False
        if target_type == 'hole':
            return 0 <= target_index <= 1
        if target_type == 'board':
            return 0 <= target_index <= self._board_target_limit()
        return False

    def legal_actions(self):
        '''
        Returns a set which corresponds to the active player's legal moves.
        '''
        active = self.button % 2
        continue_cost = self.pips[1 - active] - self.pips[active]

        actions = {FoldAction}
        if continue_cost == 0:
            actions.add(CheckAction)
            bets_forbidden = self.stacks[active] == 0 or self.stacks[1 - active] == 0
            if not bets_forbidden:
                actions.add(RaiseAction)
        else:
            actions.add(CallAction)
            raises_forbidden = (
                continue_cost >= self.stacks[active] or self.stacks[1 - active] == 0
            )
            if not raises_forbidden:
                actions.add(RaiseAction)

        if self.street < 5 and not self.redraws_used[active]:
            actions.add(RedrawAction)
        return actions

    def raise_bounds(self):
        '''
        Returns a tuple of the minimum and maximum legal raises.
        '''
        active = self.button % 2
        continue_cost = self.pips[1 - active] - self.pips[active]
        max_contribution = min(
            self.stacks[active],
            self.stacks[1 - active] + continue_cost,
        )
        min_contribution = min(
            max_contribution,
            continue_cost + max(continue_cost, BIG_BLIND),
        )
        return (self.pips[active] + min_contribution, self.pips[active] + max_contribution)

    def proceed_street(self):
        '''
        Resets pips and advances the game tree to the next betting street.
        Streets: 0 (preflop), 3 (flop), 4 (turn), 5 (river).
        '''
        if self.street == 5:
            return self.showdown()
        if self.street == 0:
            new_street = 3
        elif self.street == 3:
            new_street = 4
        else:
            new_street = 5
        return RoundState(
            1,
            new_street,
            [0, 0],
            list(self.stacks),
            [list(self.hands[0]), list(self.hands[1])],
            list(self.board),
            list(self.redraws_used),
            self,
        )

    def _proceed_betting_action(self, action):
        active = self.button % 2
        if isinstance(action, FoldAction):
            delta = self.stacks[0] - STARTING_STACK if active == 0 else STARTING_STACK - self.stacks[1]
            return TerminalState([delta, -delta], self)

        if isinstance(action, CallAction):
            if self.street == 0 and self.button == 0:
                return RoundState(
                    1,
                    0,
                    [BIG_BLIND] * 2,
                    [STARTING_STACK - BIG_BLIND] * 2,
                    [list(self.hands[0]), list(self.hands[1])],
                    list(self.board),
                    list(self.redraws_used),
                    self,
                )
            new_pips = list(self.pips)
            new_stacks = list(self.stacks)
            contribution = new_pips[1 - active] - new_pips[active]
            new_stacks[active] -= contribution
            new_pips[active] += contribution
            state = RoundState(
                self.button + 1,
                self.street,
                new_pips,
                new_stacks,
                [list(self.hands[0]), list(self.hands[1])],
                list(self.board),
                list(self.redraws_used),
                self,
            )
            return state.proceed_street()

        if isinstance(action, CheckAction):
            both_acted = (self.street == 0 and self.button > 0) or (
                self.street > 0 and self.button > 1
            )
            if both_acted:
                return self.proceed_street()
            return RoundState(
                self.button + 1,
                self.street,
                list(self.pips),
                list(self.stacks),
                [list(self.hands[0]), list(self.hands[1])],
                list(self.board),
                list(self.redraws_used),
                self,
            )

        # RaiseAction
        new_pips = list(self.pips)
        new_stacks = list(self.stacks)
        contribution = action.amount - new_pips[active]
        new_stacks[active] -= contribution
        new_pips[active] += contribution
        return RoundState(
            self.button + 1,
            self.street,
            new_pips,
            new_stacks,
            [list(self.hands[0]), list(self.hands[1])],
            list(self.board),
            list(self.redraws_used),
            self,
        )

    def proceed(self, action):
        '''
        Advances the game tree by one action performed by the active player.
        '''
        active = self.button % 2
        if isinstance(action, RedrawAction):
            target_type = action.target_type
            target_index = action.target_index
            inner_action = action.action

            if self._is_valid_redraw_target(active, target_type, target_index):
                hands = [list(self.hands[0]), list(self.hands[1])]
                board = list(self.board)
                redraws_used = list(self.redraws_used)
                # Engine does not transmit the new redraw card directly in action history.
                # Use a placeholder to preserve shape/indices in local bot state.
                # Opponent holes are unknown ([] in runner until O at showdown); pad so
                # replaying their W…K action does not index past the list end.
                if target_type == 'hole':
                    slot = list(hands[active])
                    need = max(2, target_index + 1)
                    while len(slot) < need:
                        slot.append('??')
                    slot[target_index] = '??'
                    hands[active] = slot
                else:
                    b = list(board)
                    while len(b) <= target_index:
                        b.append('??')
                    b[target_index] = '??'
                    board = b
                redraws_used[active] = True
                state_after_redraw = RoundState(
                    self.button,
                    self.street,
                    list(self.pips),
                    list(self.stacks),
                    hands,
                    board,
                    redraws_used,
                    self,
                )
                return state_after_redraw._proceed_betting_action(inner_action)
            return self._proceed_betting_action(inner_action)

        return self._proceed_betting_action(action)
