import re

import tensorflow as tf
from tensorflow.python.ops import control_flow_ops, array_ops, variable_scope as vs, init_ops

from seq2seq import dynamic_rnn
from seq2seq.dynamic_rnn import stack_bidirectional_dynamic_rnn, stack_attention_dynamic_rnn

# noinspection PyProtectedMember
_WEIGHTS_NAME = dynamic_rnn._WEIGHTS_NAME
# noinspection PyProtectedMember
_BIAS_NAME = dynamic_rnn._BIAS_NAME


def analyser_rnn(cells_fw,
                 cells_bw,
                 inputs: list,
                 sequence_length: list,
                 dtype):
    if len(inputs) != len(sequence_length):
        raise ValueError("Number of inputs and inputs lengths must be equals")
    if len(inputs) == 0:
        raise ValueError("Number of inputs must be greater zero")

    batch_size = None
    for _inputs, _sequence_length in zip(inputs, sequence_length):
        _batch_size = _inputs.get_shape()[0].value
        if batch_size is not None and _batch_size != batch_size:
            raise ValueError("Batch sizes for any inputs must be equals")
        batch_size = _sequence_length.get_shape()[0].value
        if _batch_size != batch_size:
            raise ValueError("Batch sizes of Inputs and inputs lengths must be equals")

    attention_states = []
    for i, (_inputs, _lengths) in enumerate(zip(inputs, sequence_length)):
        with vs.variable_scope("encoder_%d" % i):
            encoder_outputs, states_fw, states_bw = stack_bidirectional_dynamic_rnn(
                cells_fw,
                cells_bw,
                _inputs,
                sequence_length=_lengths,
                dtype=dtype)
            attention_states.append(tf.concat((states_fw[-1], states_bw[-1]), 2))
    return attention_states


def input_projection(inputs, projection_size, dtype):
    input_size = None
    for _inputs in inputs:
        _input_size = _inputs.get_shape()[2].value
        if input_size is not None and _input_size != input_size:
            raise ValueError("Input sizes for any inputs must be equals")
        input_size = _input_size

    with vs.variable_scope("input_projection"):
        W_enc = vs.get_variable(_WEIGHTS_NAME,
                                [input_size, projection_size],
                                dtype)
        B_enc = vs.get_variable(_BIAS_NAME,
                                [projection_size],
                                dtype,
                                init_ops.constant_initializer(0, dtype))
        projections = []
        for i, _inputs in enumerate(inputs):
            batch_size = _inputs.get_shape()[0].value
            input_length = _inputs.get_shape()[1].value
            if input_length is None:
                input_length = tf.shape(_inputs)[1]

            _inputs = tf.reshape(_inputs, [batch_size * input_length, input_size])
            projection = _inputs @ W_enc + B_enc
            projection = tf.reshape(projection, [batch_size, input_length, projection_size])
            projections.append(projection)
    return projections


def analysing_loss(word_logits,
                   word_targets,
                   token_logits,
                   token_targets,
                   variables=None,
                   scope=None,
                   l2_loss_weight=0.001,
                   except_not_weights=True,
                   except_variable_names: list = None):
    if variables is None:
        if scope is None:
            raise ValueError("Expected trainable variables or they scope")
        variables = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope)

    if except_variable_names:
        variables = [variable for variable in variables if variable.name not in except_variable_names]
    if except_not_weights:
        variables = [variable for variable in variables if re.match(r".*%s.*" % _WEIGHTS_NAME, variable.name)]
    with vs.variable_scope("analysing_loss"):
        word_loss = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=word_targets, logits=word_logits)
        token_loss = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=token_targets, logits=token_logits)
        l2_loss = l2_loss_weight * tf.reduce_sum([tf.nn.l2_loss(variable) for variable in variables])
        loss = tf.reduce_mean(tf.sqrt(tf.square(word_loss) + tf.square(token_loss) + tf.square(l2_loss)))
    return loss


def sequence_output(attention_states,
                    word_cells,
                    token_cells,
                    num_words,
                    num_tokens,
                    word_size,
                    token_size,
                    time_steps,
                    word_num_heads,
                    token_num_heads,
                    dtype):
    batch_size = None
    for state in attention_states:
        _batch_size = state.get_shape()[0].value
        if batch_size is not None and _batch_size != batch_size:
            raise ValueError("Batch sizes for any Attention states must be equals")
        batch_size = _batch_size

    with vs.variable_scope("word_decoder"):
        decoder_inputs = tf.zeros([time_steps, batch_size, word_size], dtype, "decoder_inputs")
        decoder_outputs, decoder_states = stack_attention_dynamic_rnn(
            word_cells,
            decoder_inputs,
            attention_states,
            word_size,
            word_num_heads,
            dtype=dtype)
    with vs.variable_scope("word_softmax"):
        W_sft = vs.get_variable(_WEIGHTS_NAME, [word_size, num_words], dtype)
        B_sft = vs.get_variable(_BIAS_NAME, [num_words], dtype, init_ops.constant_initializer(0, dtype))
        word_logits = tf.reshape(decoder_outputs, [time_steps * batch_size, word_size])
        word_logits = word_logits @ W_sft + B_sft
        word_outputs = tf.nn.softmax(word_logits, 1)
        word_logits = tf.reshape(word_logits, [time_steps, batch_size, num_words])
        word_outputs = tf.reshape(word_outputs, [time_steps, batch_size, num_words])
        word_logits = tf.transpose(word_logits, [1, 0, 2])
        word_outputs = tf.transpose(word_outputs, [1, 0, 2])
    with vs.variable_scope("token_decoder"):
        decoder_outputs, decoder_states = stack_attention_dynamic_rnn(
            token_cells,
            decoder_outputs,
            attention_states,
            token_size,
            token_num_heads,
            use_inputs=True,
            dtype=dtype)
    with vs.variable_scope("token_softmax"):
        W_sft = vs.get_variable(_WEIGHTS_NAME, [token_size, num_tokens], dtype)
        B_sft = vs.get_variable(_BIAS_NAME, [num_tokens], dtype, init_ops.constant_initializer(0, dtype))
        token_logits = tf.reshape(decoder_outputs, [time_steps * batch_size, token_size])
        token_logits = token_logits @ W_sft + B_sft
        token_outputs = tf.nn.softmax(token_logits, 1)
        token_outputs = tf.reshape(token_outputs, [time_steps, batch_size, num_tokens])
        token_logits = tf.reshape(token_logits, [time_steps, batch_size, num_tokens])
        token_outputs = tf.reshape(token_outputs, [time_steps, batch_size, num_tokens])
        token_logits = tf.transpose(token_logits, [1, 0, 2])
        token_outputs = tf.transpose(token_outputs, [1, 0, 2])
    return word_logits, word_outputs, token_logits, token_outputs


def tree_output(attention_states,
                root_cells,
                root_time_steps,
                root_num_heads,
                num_tokens,
                tree_height,
                dtype):
    batch_size = None
    for state in attention_states:
        _batch_size = state.get_shape()[0].value
        if batch_size is not None and _batch_size != batch_size:
            raise ValueError("Batch sizes for any Attention states must be equals")
        batch_size = _batch_size

    with vs.variable_scope("root_decoder"):
        root_decoder_inputs = tf.zeros([root_time_steps, batch_size, num_tokens], dtype, "root_decoder_inputs")
        root_decoder_outputs, root_decoder_states = stack_attention_dynamic_rnn(
            root_cells,
            root_decoder_inputs,
            attention_states,
            num_tokens,
            root_num_heads,
            dtype=dtype)
        roots = root_decoder_states[-1]
        labels = root_decoder_outputs[-1]
        num_roots = roots.get_shape()[0].value
        if num_roots is None:
            num_roots = array_ops.shape(roots)[0]
        state_size = roots.get_shape()[2].value
        states_ta_size = 2 ** tree_height - 1

    with vs.variable_scope("tree_decoder"):
        bias_initializer = init_ops.constant_initializer(0, dtype)
        with vs.variable_scope("TreeOutputProjection"):
            W_out = vs.get_variable(_WEIGHTS_NAME, [state_size, num_tokens], dtype)
            B_out = vs.get_variable(_BIAS_NAME, [num_tokens], dtype, bias_initializer)
        with vs.variable_scope("TreeLeftStateProjection"):
            W_left = vs.get_variable(_WEIGHTS_NAME, [state_size, state_size], dtype)
            B_left = vs.get_variable(_BIAS_NAME, [state_size], dtype, bias_initializer)
        with vs.variable_scope("TreeRightStateProjection"):
            W_right = vs.get_variable(_WEIGHTS_NAME, [state_size, state_size], dtype)
            B_right = vs.get_variable(_BIAS_NAME, [state_size], dtype, bias_initializer)
        with vs.variable_scope("Arrays"):
            states_ta = tf.TensorArray(
                dtype,
                size=states_ta_size,
                tensor_array_name="states",
                element_shape=roots.get_shape(),
                clear_after_read=False
            )

        def _time_step(time, size, _states_ta):
            _state = _states_ta.read(time)
            _state = tf.reshape(_state, [num_roots * batch_size, state_size])
            _left_state = _state @ W_left + B_left
            _right_state = _state @ W_right + B_right
            _left_state = tf.reshape(_left_state, [num_roots, batch_size, state_size])
            _right_state = tf.reshape(_right_state, [num_roots, batch_size, state_size])
            _states_ta = _states_ta.write(size, _left_state)
            _states_ta = _states_ta.write(size + 1, _right_state)
            return time + 1, size + 2, _states_ta

        states_ta = states_ta.write(0, roots)
        _, _, states_ta = control_flow_ops.while_loop(
            cond=lambda _time, *_: _time < 2 ** (tree_height - 1) - 1,
            body=_time_step,
            loop_vars=(0, 1, states_ta))

        with vs.variable_scope("OutputsProjection"):
            states = states_ta.stack()
            states = tf.reshape(states, [states_ta_size * num_roots * batch_size, state_size])
            outputs = states @ W_out + B_out
            outputs = tf.reshape(outputs, [states_ta_size, num_roots, batch_size, num_tokens])
    return outputs


def main():
    attention_states = [tf.random_normal([11, 217, 233]) for _ in range(6)]
    from tensorflow.python.ops.rnn_cell_impl import GRUCell
    root_cells = [GRUCell(123) for _ in range(3)]
    root_time_steps = 10
    root_num_heads = 4
    num_tokens = 333
    tree_height = 5
    dtype = tf.float32
    out = tree_output(attention_states,
                      root_cells,
                      root_time_steps,
                      root_num_heads,
                      num_tokens,
                      tree_height,
                      dtype)
    with tf.Session() as dssa:
        dssa.run(tf.global_variables_initializer())
        outs = dssa.run([out])
        print(outs)


if __name__ == '__main__':
    main()
