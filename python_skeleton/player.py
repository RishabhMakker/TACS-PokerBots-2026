'''
Simple example pokerbot, written in Python.
'''
from skeleton.actions import FoldAction, CallAction, CheckAction, RaiseAction, RedrawAction
from skeleton.states import NUM_ROUNDS, STARTING_STACK
from skeleton.bot import Bot
from skeleton.runner import parse_args, run_bot

import random


RANK_ORDER = '23456789TJQKA'


class Player(Bot):
    '''
    A pokerbot.
    '''

    def __init__(self):
        self.last_round_num = 0
        self.bankroll = 0
        self.game_clock = 0.0
        self.round_num = 0
        self.is_button = False
        self.my_cards = []
        self.opp_cards = []
        self.my_delta = 0

    def _card_rank_value(self, card):
        if not card or len(card) < 1 or card == '??':
            return -1
        try:
            return RANK_ORDER.index(card[0])
        except ValueError:
            return -1

    def _weakest_hole_index(self, my_cards):
        values = [self._card_rank_value(card) for card in my_cards]
        return 0 if values[0] <= values[1] else 1

    def _should_redraw(self, round_state, active):
        if round_state.redraws_used[active]:
            return False
        if round_state.street not in (3, 4):
            return False
        my_cards = round_state.hands[active]
        min_rank = min(self._card_rank_value(my_cards[0]), self._card_rank_value(my_cards[1]))
        max_rank = max(self._card_rank_value(my_cards[0]), self._card_rank_value(my_cards[1]))
        # Heuristic: redraw when both hole cards are weak.
        return max_rank <= RANK_ORDER.index('9') and min_rank <= RANK_ORDER.index('7')

    def handle_new_round(self, game_state, round_state, active):
        '''
        Called when a new round starts. Called NUM_ROUNDS times.
        '''
        self.last_round_num = game_state.round_num
        self.round_num = game_state.round_num
        self.bankroll = game_state.bankroll
        self.game_clock = game_state.game_clock
        self.is_button = (active == 0)
        self.my_cards = list(round_state.hands[active])

    def handle_round_over(self, game_state, terminal_state, active):
        '''
        Called when a round ends. Called NUM_ROUNDS times.
        '''
        self.bankroll = game_state.bankroll
        self.game_clock = game_state.game_clock
        self.my_delta = terminal_state.deltas[active]
        self.opp_cards = list(terminal_state.previous_state.hands[1 - active])

    def get_action(self, game_state, round_state, active):
        '''
        Where the magic happens - your code should implement this function.
        Called any time the engine needs an action from your bot.
        '''
        # ---- State extraction (from all 3 parameters) ----
        game_clock = game_state.game_clock
        bankroll = game_state.bankroll
        round_num = game_state.round_num

        legal_actions = round_state.legal_actions()
        my_cards = round_state.hands[active]
        board = round_state.board
        street = round_state.street
        my_pip = round_state.pips[active]
        opp_pip = round_state.pips[1 - active]
        my_stack = round_state.stacks[active]
        opp_stack = round_state.stacks[1 - active]
        continue_cost = opp_pip - my_pip
        pot = (STARTING_STACK - my_stack) + (STARTING_STACK - opp_stack)
        pot_odds = continue_cost / (pot + continue_cost) if continue_cost > 0 else 0
        min_raise, max_raise = round_state.raise_bounds() if RaiseAction in legal_actions else (0, 0)
        can_redraw = RedrawAction in legal_actions
        redraws_used = round_state.redraws_used

        # ---- Decision (TODO: replace with equity-based strategy) ----
        if RedrawAction in legal_actions and self._should_redraw(round_state, active):
            target_index = self._weakest_hole_index(round_state.hands[active])
            if CheckAction in legal_actions:
                return RedrawAction('hole', target_index, CheckAction())
            if CallAction in legal_actions:
                return RedrawAction('hole', target_index, CallAction())
            if RaiseAction in legal_actions:
                return RedrawAction('hole', target_index, RaiseAction(min_raise))

        if RaiseAction in legal_actions and continue_cost == 0 and random.random() < 0.3:
            return RaiseAction(min_raise)
        if CheckAction in legal_actions:
            return CheckAction()
        if continue_cost > 12 and random.random() < 0.35:
            return FoldAction()
        return CallAction()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
