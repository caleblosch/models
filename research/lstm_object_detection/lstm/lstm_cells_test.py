# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
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
# ==============================================================================
"""Tests for lstm_object_detection.lstm.lstm_cells."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf

from lstm_object_detection.lstm import lstm_cells


class BottleneckConvLstmCellsTest(tf.test.TestCase):

  def test_run_lstm_cell(self):
    filter_size = [3, 3]
    output_size = [10, 10]
    num_units = 15
    state_name = 'lstm_state'
    batch_size = 4
    dtype = tf.float32
    learned_state = False

    inputs = tf.zeros([4, 10, 10, 3], dtype=tf.float32)
    cell = lstm_cells.BottleneckConvLSTMCell(
        filter_size=filter_size,
        output_size=output_size,
        num_units=num_units)
    init_state = cell.init_state(
        state_name, batch_size, dtype, learned_state)
    output, state_tuple = cell(inputs, init_state)
    self.assertAllEqual([4, 10, 10, 15], output.shape.as_list())
    self.assertAllEqual([4, 10, 10, 15], state_tuple[0].shape.as_list())
    self.assertAllEqual([4, 10, 10, 15], state_tuple[1].shape.as_list())

  def test_run_lstm_cell_with_flattened_state(self):
    filter_size = [3, 3]
    output_dim = 10
    output_size = [output_dim] * 2
    num_units = 15
    state_name = 'lstm_state'
    batch_size = 4
    dtype = tf.float32
    learned_state = False

    inputs = tf.zeros([batch_size, output_dim, output_dim, 3], dtype=tf.float32)
    cell = lstm_cells.BottleneckConvLSTMCell(
        filter_size=filter_size,
        output_size=output_size,
        num_units=num_units,
        flattened_state=True)
    init_state = cell.init_state(
        state_name, batch_size, dtype, learned_state)
    output, state_tuple = cell(inputs, init_state)
    self.assertAllEqual([4, 1500], output.shape.as_list())
    self.assertAllEqual([4, 1500], state_tuple[0].shape.as_list())
    self.assertAllEqual([4, 1500], state_tuple[1].shape.as_list())

  def test_get_init_state(self):
    filter_size = [3, 3]
    output_dim = 10
    output_size = [output_dim] * 2
    num_units = 15
    state_name = 'lstm_state'
    batch_size = 4
    dtype = tf.float32
    learned_state = False

    cell = lstm_cells.BottleneckConvLSTMCell(
        filter_size=filter_size,
        output_size=output_size,
        num_units=num_units)
    init_c, init_h = cell.init_state(
        state_name, batch_size, dtype, learned_state)

    self.assertEqual(tf.float32, init_c.dtype)
    self.assertEqual(tf.float32, init_h.dtype)
    with self.test_session() as sess:
      init_c_res, init_h_res = sess.run([init_c, init_h])
      self.assertAllClose(np.zeros((4, 10, 10, 15)), init_c_res)
      self.assertAllClose(np.zeros((4, 10, 10, 15)), init_h_res)

  def test_get_init_learned_state(self):
    filter_size = [3, 3]
    output_size = [10, 10]
    num_units = 15
    state_name = 'lstm_state'
    batch_size = 4
    dtype = tf.float32
    learned_state = True

    cell = lstm_cells.BottleneckConvLSTMCell(
        filter_size=filter_size,
        output_size=output_size,
        num_units=num_units)
    init_c, init_h = cell.init_state(
        state_name, batch_size, dtype, learned_state)

    self.assertEqual(tf.float32, init_c.dtype)
    self.assertEqual(tf.float32, init_h.dtype)
    self.assertAllEqual([4, 10, 10, 15], init_c.shape.as_list())
    self.assertAllEqual([4, 10, 10, 15], init_h.shape.as_list())


if __name__ == '__main__':
  tf.test.main()
