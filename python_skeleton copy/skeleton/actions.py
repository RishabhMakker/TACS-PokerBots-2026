'''
The actions that the player is allowed to take.
'''
from collections import namedtuple

FoldAction = namedtuple('FoldAction', [])
CallAction = namedtuple('CallAction', [])
CheckAction = namedtuple('CheckAction', [])
RaiseAction = namedtuple('RaiseAction', ['amount'])
# Redraw is combined with a betting action.
# target_type: 'hole' or 'board'
# target_index: 0-1 for hole, 0-4 for board (street dependent)
# action: FoldAction | CallAction | CheckAction | RaiseAction
RedrawAction = namedtuple('RedrawAction', ['target_type', 'target_index', 'action'])
