# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import paddle
import paddle.nn as nn

import paddle.optimizer as optimizer
import paddle.regularizer as regularizer
from lib.utils.workspace import register, serializable

__all__ = ['LearningRate', 'OptimizerBuilder']

from ..utils.logger import setup_logger
logger = setup_logger(__name__)


@serializable
class PiecewiseDecay(object):
    """
    Multi step learning rate decay

    Args:
        gamma (float | list): decay factor
        milestones (list): steps at which to decay learning rate
    """

    def __init__(self,
                 gamma=[0.1, 0.01],
                 milestones=[8, 11],
                 values=None,
                 use_warmup=True):
        super(PiecewiseDecay, self).__init__()
        if type(gamma) is not list:
            self.gamma = []
            for i in range(len(milestones)):
                self.gamma.append(gamma / 10**i)
        else:
            self.gamma = gamma
        self.milestones = milestones
        self.values = values
        self.use_warmup = use_warmup

    def __call__(self,
                 base_lr=None,
                 boundary=None,
                 value=None,
                 step_per_epoch=None):
        if boundary is not None and self.use_warmup:
            boundary.extend([int(step_per_epoch) * i for i in self.milestones])
        else:
            # do not use LinearWarmup
            boundary = [int(step_per_epoch) * i for i in self.milestones]
            value = [base_lr]  # during step[0, boundary[0]] is base_lr

        # self.values is setted directly in config 
        if self.values is not None:
            assert len(self.milestones) + 1 == len(self.values)
            return optimizer.lr.PiecewiseDecay(boundary, self.values)

        # value is computed by self.gamma
        value = value if value is not None else [base_lr]
        for i in self.gamma:
            value.append(base_lr * i)

        return optimizer.lr.PiecewiseDecay(boundary, value)


@serializable
class LinearWarmup(object):
    """
    Warm up learning rate linearly

    Args:
        steps (int): warm up steps
        start_factor (float): initial learning rate factor
    """

    def __init__(self, steps=500, start_factor=1. / 3):
        super(LinearWarmup, self).__init__()
        self.steps = steps
        self.start_factor = start_factor

    def __call__(self, base_lr, step_per_epoch):
        boundary = []
        value = []
        for i in range(self.steps + 1):
            if self.steps > 0:
                alpha = i / self.steps
                factor = self.start_factor * (1 - alpha) + alpha
                lr = base_lr * factor
                value.append(lr)
            if i > 0:
                boundary.append(i)
        return boundary, value


@register
class LearningRate(object):
    """
    Learning Rate configuration

    Args:
        base_lr (float): base learning rate
        schedulers (list): learning rate schedulers
    """
    __category__ = 'optim'

    def __init__(self,
                 base_lr=0.01,
                 schedulers=[PiecewiseDecay(), LinearWarmup()]):
        super(LearningRate, self).__init__()
        self.base_lr = base_lr
        self.schedulers = schedulers

    def __call__(self, step_per_epoch):
        assert len(self.schedulers) >= 1
        if not self.schedulers[0].use_warmup:
            return self.schedulers[0](base_lr=self.base_lr,
                                      step_per_epoch=step_per_epoch)

        # TODO: split warmup & decay 
        # warmup
        boundary, value = self.schedulers[1](self.base_lr, step_per_epoch)
        # decay
        decay_lr = self.schedulers[0](self.base_lr, boundary, value,
                                      step_per_epoch)
        return decay_lr


@register
class OptimizerBuilder():
    """
    Build optimizer handles
    Args:
        regularizer (object): an `Regularizer` instance
        optimizer (object): an `Optimizer` instance
    """
    __category__ = 'optim'

    def __init__(self,
                 clip_grad_by_norm=None,
                 regularizer={'type': 'L2',
                              'factor': .0001},
                 optimizer={'type': 'Momentum',
                            'momentum': .9}):
        self.clip_grad_by_norm = clip_grad_by_norm
        self.regularizer = regularizer
        self.optimizer = optimizer

    def __call__(self, learning_rate, model=None):
        if not isinstance(model, (list, tuple)):
            model = [model]

        if self.clip_grad_by_norm is not None:
            grad_clip = nn.ClipGradByGlobalNorm(
                clip_norm=self.clip_grad_by_norm)
        else:
            grad_clip = None
        if self.regularizer and self.regularizer != 'None':
            reg_type = self.regularizer['type'] + 'Decay'
            reg_factor = self.regularizer['factor']
            regularization = getattr(regularizer, reg_type)(reg_factor)
        else:
            regularization = None

        optim_args = self.optimizer.copy()
        optim_type = optim_args['type']
        del optim_args['type']
        optim_args['weight_decay'] = regularization
        op = getattr(optimizer, optim_type)

        params = []
        for m in model:
            if m is not None:
                params.extend(m.parameters())

        return op(learning_rate=learning_rate,
                  parameters=params,
                  grad_clip=grad_clip,
                  **optim_args)
