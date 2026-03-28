'''
The infrastructure for interacting with the engine.
'''
import argparse
import socket
from .actions import FoldAction, CallAction, CheckAction, RaiseAction, RedrawAction
from .states import GameState, TerminalState, RoundState
from .states import STARTING_STACK, BIG_BLIND, SMALL_BLIND
from .bot import Bot


class Runner():
    '''
    Interacts with the engine.
    '''

    def __init__(self, pokerbot, socketfile):
        self.pokerbot = pokerbot
        self.socketfile = socketfile
        # Redraw metadata for the next action by a given actor index.
        self._pending_redraw = {0: None, 1: None}
        self._pending_redraw_old_card = {0: None, 1: None}

    def receive(self):
        '''
        Generator for incoming messages from the engine.
        '''
        while True:
            packet = self.socketfile.readline().strip().split(' ')
            if not packet:
                break
            yield packet

    def _encode_basic_action(self, action):
        if isinstance(action, FoldAction):
            return 'F'
        if isinstance(action, CallAction):
            return 'C'
        if isinstance(action, CheckAction):
            return 'K'
        return 'R' + str(action.amount)

    def send(self, action):
        '''
        Encodes an action and sends it to the engine.
        '''
        if isinstance(action, RedrawAction):
            target_char = 'H' if action.target_type == 'hole' else 'B'
            code = 'W{}{}{}'.format(
                target_char,
                int(action.target_index),
                self._encode_basic_action(action.action),
            )
        else:
            code = self._encode_basic_action(action)
        self.socketfile.write(code + '\n')
        self.socketfile.flush()

    @staticmethod
    def _decode_basic_action(clause):
        code = clause[0]
        if code == 'F':
            return FoldAction()
        if code == 'C':
            return CallAction()
        if code == 'K':
            return CheckAction()
        return RaiseAction(int(float(clause[1:])))

    def _apply_action_clause(self, round_state, action_clause):
        actor = round_state.button % 2
        basic_action = self._decode_basic_action(action_clause)
        redraw_info = self._pending_redraw.get(actor)
        if redraw_info is not None:
            target_type, target_index = redraw_info
            action = RedrawAction(target_type, target_index, basic_action)
            self._pending_redraw[actor] = None
            self._pending_redraw_old_card[actor] = None
            return round_state.proceed(action)
        return round_state.proceed(basic_action)

    def run(self):
        '''
        Reconstructs the game tree based on the action history received from the engine.
        '''
        game_state = GameState(0, 0., 1)
        round_state = None
        active = 0
        round_flag = True
        for packet in self.receive():
            for clause in packet:
                code = clause[0]
                if code == 'T':
                    game_state = GameState(game_state.bankroll, float(clause[1:]), game_state.round_num)
                elif code == 'P':
                    active = int(float(clause[1:]))
                elif code == 'H':
                    hands = [['??', '??'], ['??', '??']]
                    hands[active] = clause[1:].split(',')
                    pips = [SMALL_BLIND, BIG_BLIND]
                    stacks = [STARTING_STACK - SMALL_BLIND, STARTING_STACK - BIG_BLIND]
                    round_state = RoundState(0, 0, pips, stacks, hands, [], [False, False], None)
                    self._pending_redraw = {0: None, 1: None}
                    self._pending_redraw_old_card = {0: None, 1: None}
                elif code == 'G':
                    if round_flag:
                        self.pokerbot.handle_new_round(game_state, round_state, active)
                        round_flag = False
                elif code == 'W':
                    # Opponent redraw notification: WH0 / WB2
                    if len(clause) >= 3 and clause[2].isdigit():
                        target_code = clause[1]
                        target_index = int(clause[2])
                        if target_code in ('H', 'B'):
                            target_type = 'hole' if target_code == 'H' else 'board'
                            actor = 1 - active
                            self._pending_redraw[actor] = (target_type, target_index)
                elif code == 'X':
                    # Revealed old redraw card for opponent's redraw.
                    actor = 1 - active
                    self._pending_redraw_old_card[actor] = clause[1:]
                elif code in ('F', 'C', 'K', 'R'):
                    round_state = self._apply_action_clause(round_state, clause)
                elif code == 'B':
                    board_cards = clause[1:].split(',') if len(clause) > 1 else []
                    round_state = RoundState(
                        round_state.button,
                        round_state.street,
                        round_state.pips,
                        round_state.stacks,
                        round_state.hands,
                        board_cards,
                        round_state.redraws_used,
                        round_state.previous_state,
                    )
                elif code == 'O':
                    # backtrack
                    round_state = round_state.previous_state
                    revised_hands = [list(round_state.hands[0]), list(round_state.hands[1])]
                    revised_hands[1 - active] = clause[1:].split(',')
                    round_state = RoundState(
                        round_state.button,
                        round_state.street,
                        round_state.pips,
                        round_state.stacks,
                        revised_hands,
                        round_state.board,
                        round_state.redraws_used,
                        round_state.previous_state,
                    )
                    round_state = TerminalState([0, 0], round_state)
                elif code == 'A':
                    assert isinstance(round_state, TerminalState)
                    delta = int(float(clause[1:]))
                    deltas = [-delta, -delta]
                    deltas[active] = delta
                    round_state = TerminalState(deltas, round_state.previous_state)
                    self.pokerbot.handle_round_over(game_state, round_state, active)
                    game_state = GameState(game_state.bankroll + delta, game_state.game_clock, game_state.round_num + 1)
                    round_flag = True
                elif code == 'Q':
                    return

            if round_flag or isinstance(round_state, TerminalState):
                self.send(CheckAction())
            else:
                action = self.pokerbot.get_action(game_state, round_state, active)
                if isinstance(action, RedrawAction):
                    self._pending_redraw[active] = (action.target_type, int(action.target_index))
                self.send(action)


def parse_args():
    '''
    Parses arguments corresponding to socket connection information.
    '''
    parser = argparse.ArgumentParser(prog='python3 player.py')
    parser.add_argument('--host', type=str, default='localhost', help='Host to connect to, defaults to localhost')
    parser.add_argument('port', type=int, help='Port on host to connect to')
    return parser.parse_args()


def run_bot(pokerbot, args):
    '''
    Runs the pokerbot.
    '''
    assert isinstance(pokerbot, Bot)
    try:
        sock = socket.create_connection((args.host, args.port))
    except OSError:
        print('Could not connect to {}:{}'.format(args.host, args.port))
        return
    socketfile = sock.makefile('rw')
    runner = Runner(pokerbot, socketfile)
    runner.run()
    socketfile.close()
    sock.close()
