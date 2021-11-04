"""Tests for google3.third_party.tensorflow_models.object_detection.meta_architectures.deepmac_meta_arch."""

import functools
import unittest

from absl.testing import parameterized
import numpy as np
import tensorflow as tf

from object_detection.core import losses
from object_detection.core import preprocessor
from object_detection.meta_architectures import center_net_meta_arch
from object_detection.meta_architectures import deepmac_meta_arch
from object_detection.utils import tf_version


class DummyFeatureExtractor(center_net_meta_arch.CenterNetFeatureExtractor):

  def __init__(self,
               channel_means,
               channel_stds,
               bgr_ordering,
               num_feature_outputs,
               stride):
    self._num_feature_outputs = num_feature_outputs
    self._stride = stride
    super(DummyFeatureExtractor, self).__init__(
        channel_means=channel_means, channel_stds=channel_stds,
        bgr_ordering=bgr_ordering)

  def predict(self):
    pass

  def loss(self):
    pass

  def postprocess(self):
    pass

  def call(self, inputs):
    batch_size, input_height, input_width, _ = inputs.shape
    fake_output = tf.ones([
        batch_size, input_height // self._stride, input_width // self._stride,
        64
    ], dtype=tf.float32)
    return [fake_output] * self._num_feature_outputs

  @property
  def out_stride(self):
    return self._stride

  @property
  def num_feature_outputs(self):
    return self._num_feature_outputs


class MockMaskNet(tf.keras.layers.Layer):

  def __call__(self, instance_embedding, pixel_embedding, training):
    return tf.zeros_like(pixel_embedding[:, :, :, 0]) + 0.9


def build_meta_arch(predict_full_resolution_masks=False, use_dice_loss=False,
                    use_instance_embedding=True, mask_num_subsamples=-1,
                    network_type='hourglass10', use_xy=True,
                    pixel_embedding_dim=2,
                    dice_loss_prediction_probability=False,
                    color_consistency_threshold=0.5):
  """Builds the DeepMAC meta architecture."""

  feature_extractor = DummyFeatureExtractor(
      channel_means=(1.0, 2.0, 3.0),
      channel_stds=(10., 20., 30.),
      bgr_ordering=False,
      num_feature_outputs=2,
      stride=4)
  image_resizer_fn = functools.partial(
      preprocessor.resize_to_range,
      min_dimension=128,
      max_dimension=128,
      pad_to_max_dimesnion=True)

  object_center_params = center_net_meta_arch.ObjectCenterParams(
      classification_loss=losses.WeightedSigmoidClassificationLoss(),
      object_center_loss_weight=1.0,
      min_box_overlap_iou=1.0,
      max_box_predictions=5,
      use_labeled_classes=False)

  if use_dice_loss:
    classification_loss = losses.WeightedDiceClassificationLoss(
        squared_normalization=False,
        is_prediction_probability=dice_loss_prediction_probability)
  else:
    classification_loss = losses.WeightedSigmoidClassificationLoss()

  deepmac_params = deepmac_meta_arch.DeepMACParams(
      classification_loss=classification_loss,
      dim=8,
      task_loss_weight=1.0,
      pixel_embedding_dim=pixel_embedding_dim,
      allowed_masked_classes_ids=[],
      mask_size=16,
      mask_num_subsamples=mask_num_subsamples,
      use_xy=use_xy,
      network_type=network_type,
      use_instance_embedding=use_instance_embedding,
      num_init_channels=8,
      predict_full_resolution_masks=predict_full_resolution_masks,
      postprocess_crop_size=128,
      max_roi_jitter_ratio=0.0,
      roi_jitter_mode='random',
      box_consistency_loss_weight=1.0,
      color_consistency_threshold=color_consistency_threshold,
      color_consistency_dilation=2,
      color_consistency_loss_weight=1.0
  )

  object_detection_params = center_net_meta_arch.ObjectDetectionParams(
      localization_loss=losses.L1LocalizationLoss(),
      offset_loss_weight=1.0,
      scale_loss_weight=0.1
  )

  return deepmac_meta_arch.DeepMACMetaArch(
      is_training=True,
      add_summaries=False,
      num_classes=6,
      feature_extractor=feature_extractor,
      object_center_params=object_center_params,
      deepmac_params=deepmac_params,
      object_detection_params=object_detection_params,
      image_resizer_fn=image_resizer_fn)


@unittest.skipIf(tf_version.is_tf1(), 'Skipping TF2.X only test.')
class DeepMACUtilsTest(tf.test.TestCase, parameterized.TestCase):

  def test_subsample_trivial(self):
    """Test subsampling masks."""

    boxes = np.arange(4).reshape(4, 1) * np.ones((4, 4))
    masks = np.arange(4).reshape(4, 1, 1) * np.ones((4, 32, 32))
    weights = np.ones(4)
    classes = tf.one_hot(tf.range(4), depth=4)

    result = deepmac_meta_arch.subsample_instances(
        classes, weights, boxes, masks, 4)
    self.assertAllClose(result[0], classes)
    self.assertAllClose(result[1], weights)
    self.assertAllClose(result[2], boxes)
    self.assertAllClose(result[3], masks)

  def test_fill_boxes(self):

    boxes = tf.constant([[0., 0., 0.5, 0.5], [0.5, 0.5, 1.0, 1.0]])

    filled_boxes = deepmac_meta_arch.fill_boxes(boxes, 32, 32)
    expected = np.zeros((2, 32, 32))
    expected[0, :17, :17] = 1.0
    expected[1, 16:, 16:] = 1.0

    self.assertAllClose(expected, filled_boxes.numpy(), rtol=1e-3)

  def test_crop_and_resize_instance_masks(self):

    boxes = tf.zeros((5, 4))
    masks = tf.zeros((5, 128, 128))
    output = deepmac_meta_arch.crop_and_resize_instance_masks(
        masks, boxes, 32)
    self.assertEqual(output.shape, (5, 32, 32))

  def test_crop_and_resize_feature_map(self):

    boxes = tf.zeros((5, 4))
    features = tf.zeros((128, 128, 7))
    output = deepmac_meta_arch.crop_and_resize_feature_map(
        features, boxes, 32)
    self.assertEqual(output.shape, (5, 32, 32, 7))

  def test_embedding_projection_prob_shape(self):
    dist = deepmac_meta_arch.embedding_projection(
        tf.ones((4, 32, 32, 8)), tf.zeros((4, 32, 32, 8)))
    self.assertEqual(dist.shape, (4, 32, 32, 1))

  @parameterized.parameters([1e-20, 1e20])
  def test_embedding_projection_value(self, value):
    dist = deepmac_meta_arch.embedding_projection(
        tf.zeros((1, 1, 1, 8)), value + tf.zeros((1, 1, 1, 8))).numpy()
    max_float = np.finfo(dist.dtype).max
    self.assertLess(dist.max(), max_float)
    self.assertGreater(dist.max(), -max_float)

  @parameterized.named_parameters(
      [('no_conv_shortcut', (False,)),
       ('conv_shortcut', (True,))]
      )
  def test_res_dense_block(self, conv_shortcut):

    net = deepmac_meta_arch.DenseResidualBlock(32, conv_shortcut)
    out = net(tf.zeros((2, 32)))
    self.assertEqual(out.shape, (2, 32))

  @parameterized.parameters(
      [4, 8, 20]
  )
  def test_dense_resnet(self, num_layers):

    net = deepmac_meta_arch.DenseResNet(num_layers, 16, 8)
    out = net(tf.zeros((2, 24)))
    self.assertEqual(out.shape, (2, 8))

  def test_generate_2d_neighbors_shape(self):

    inp = tf.zeros((13, 14, 3))
    out = deepmac_meta_arch.generate_2d_neighbors(inp)
    self.assertEqual((8, 13, 14, 3), out.shape)

  def test_generate_2d_neighbors(self):

    inp = np.arange(16).reshape(4, 4).astype(np.float32)
    inp = tf.stack([inp, inp * 2], axis=2)
    out = deepmac_meta_arch.generate_2d_neighbors(inp, dilation=1)
    self.assertEqual((8, 4, 4, 2), out.shape)

    for i in range(2):
      expected = np.array([0, 1, 2, 4, 6, 8, 9, 10]) * (i + 1)
      self.assertAllEqual(out[:, 1, 1, i], expected)

      expected = np.array([1, 2, 3, 5, 7, 9, 10, 11]) * (i + 1)
      self.assertAllEqual(out[:, 1, 2, i], expected)

      expected = np.array([4, 5, 6, 8, 10, 12, 13, 14]) * (i + 1)
      self.assertAllEqual(out[:, 2, 1, i], expected)

      expected = np.array([5, 6, 7, 9, 11, 13, 14, 15]) * (i + 1)
      self.assertAllEqual(out[:, 2, 2, i], expected)

  def test_generate_2d_neighbors_dilation2(self):

    inp = np.arange(16).reshape(4, 4, 1).astype(np.float32)
    out = deepmac_meta_arch.generate_2d_neighbors(inp, dilation=2)
    self.assertEqual((8, 4, 4, 1), out.shape)

    expected = np.array([0, 0, 0, 0, 2, 0, 8, 10])
    self.assertAllEqual(out[:, 0, 0, 0], expected)

  def test_dilated_similarity_shape(self):

    fmap = tf.zeros((32, 32, 9))
    similarity = deepmac_meta_arch.dilated_cross_pixel_similarity(
        fmap)
    self.assertEqual((8, 32, 32), similarity.shape)

  def test_dilated_similarity(self):

    fmap = np.zeros((5, 5, 2), dtype=np.float32)

    fmap[0, 0, :] = 1.0
    fmap[4, 4, :] = 1.0

    similarity = deepmac_meta_arch.dilated_cross_pixel_similarity(
        fmap, theta=1.0, dilation=2)
    self.assertAlmostEqual(similarity.numpy()[0, 2, 2],
                           np.exp(-np.sqrt(2)))

  def test_dilated_same_instance_mask_shape(self):

    instances = tf.zeros((5, 32, 32))
    output = deepmac_meta_arch.dilated_cross_same_mask_label(instances)
    self.assertEqual((8, 5, 32, 32), output.shape)

  def test_dilated_same_instance_mask(self):

    instances = np.zeros((2, 5, 5), dtype=np.float32)
    instances[0, 0, 0] = 1.0
    instances[0, 2, 2] = 1.0
    instances[0, 4, 4] = 1.0
    output = deepmac_meta_arch.dilated_cross_same_mask_label(instances).numpy()
    self.assertAllClose(np.ones((8, 5, 5)), output[:, 1, :, :])
    self.assertAllClose([1, 0, 0, 0, 0, 0, 0, 1], output[:, 0, 2, 2])


@unittest.skipIf(tf_version.is_tf1(), 'Skipping TF2.X only test.')
class DeepMACMaskHeadTest(tf.test.TestCase, parameterized.TestCase):

  @parameterized.parameters(
      ['hourglass10', 'hourglass20', 'resnet4'])
  def test_mask_network(self, head_type):
    net = deepmac_meta_arch.MaskHeadNetwork(head_type, 8)

    out = net(tf.zeros((2, 4)), tf.zeros((2, 32, 32, 16)), training=True)
    self.assertEqual(out.shape, (2, 32, 32))

  def test_mask_network_params_resnet4(self):
    net = deepmac_meta_arch.MaskHeadNetwork('resnet4', num_init_channels=8)
    _ = net(tf.zeros((2, 16)), tf.zeros((2, 32, 32, 16)), training=True)

    trainable_params = tf.reduce_sum([tf.reduce_prod(tf.shape(w)) for w in
                                      net.trainable_weights])

    self.assertEqual(trainable_params.numpy(), 8665)

  def test_mask_network_resnet_tf_function(self):

    net = deepmac_meta_arch.MaskHeadNetwork('resnet8')
    call_func = tf.function(net.__call__)

    out = call_func(tf.zeros((2, 4)), tf.zeros((2, 32, 32, 16)), training=True)
    self.assertEqual(out.shape, (2, 32, 32))

  def test_mask_network_embedding_projection_zero(self):

    net = deepmac_meta_arch.MaskHeadNetwork(
        'embedding_projection', num_init_channels=8,
        use_instance_embedding=False)
    call_func = tf.function(net.__call__)

    out = call_func(tf.zeros((2, 7)), tf.zeros((2, 32, 32, 7)), training=True)
    self.assertEqual(out.shape, (2, 32, 32))
    self.assertAllGreater(out.numpy(), -np.inf)
    self.assertAllLess(out.numpy(), np.inf)

  def test_mask_network_embedding_projection_small(self):

    net = deepmac_meta_arch.MaskHeadNetwork(
        'embedding_projection', num_init_channels=-1,
        use_instance_embedding=False)
    call_func = tf.function(net.__call__)

    out = call_func(1e6 + tf.zeros((2, 7)),
                    tf.zeros((2, 32, 32, 7)), training=True)
    self.assertEqual(out.shape, (2, 32, 32))
    self.assertAllGreater(out.numpy(), -np.inf)
    self.assertAllLess(out.numpy(), np.inf)


@unittest.skipIf(tf_version.is_tf1(), 'Skipping TF2.X only test.')
class DeepMACMetaArchTest(tf.test.TestCase, parameterized.TestCase):

  def setUp(self):  # pylint:disable=g-missing-super-call
    self.model = build_meta_arch()

  def test_get_mask_head_input(self):

    boxes = tf.constant([[0., 0., 0.25, 0.25], [0.75, 0.75, 1.0, 1.0]],
                        dtype=tf.float32)

    pixel_embedding = np.zeros((32, 32, 4), dtype=np.float32)
    pixel_embedding[:16, :16] = 1.0
    pixel_embedding[16:, 16:] = 2.0
    pixel_embedding = tf.constant(pixel_embedding)

    mask_inputs = self.model._get_mask_head_input(boxes, pixel_embedding)
    self.assertEqual(mask_inputs.shape, (2, 16, 16, 6))

    y_grid, x_grid = tf.meshgrid(np.linspace(-1.0, 1.0, 16),
                                 np.linspace(-1.0, 1.0, 16), indexing='ij')
    for i in range(2):
      mask_input = mask_inputs[i]
      self.assertAllClose(y_grid, mask_input[:, :, 0])
      self.assertAllClose(x_grid, mask_input[:, :, 1])
      pixel_embedding = mask_input[:, :, 2:]
      self.assertAllClose(np.zeros((16, 16, 4)) + i + 1, pixel_embedding)

  def test_get_mask_head_input_no_crop_resize(self):

    model = build_meta_arch(predict_full_resolution_masks=True)
    boxes = tf.constant([[0., 0., 1.0, 1.0], [0.0, 0.0, 0.5, 1.0]],
                        dtype=tf.float32)

    pixel_embedding_np = np.random.randn(32, 32, 4).astype(np.float32)
    pixel_embedding = tf.constant(pixel_embedding_np)

    mask_inputs = model._get_mask_head_input(boxes, pixel_embedding)
    self.assertEqual(mask_inputs.shape, (2, 32, 32, 6))

    y_grid, x_grid = tf.meshgrid(np.linspace(.0, 1.0, 32),
                                 np.linspace(.0, 1.0, 32), indexing='ij')

    ys = [0.5, 0.25]
    xs = [0.5, 0.5]
    for i in range(2):
      mask_input = mask_inputs[i]
      self.assertAllClose(y_grid - ys[i], mask_input[:, :, 0])
      self.assertAllClose(x_grid - xs[i], mask_input[:, :, 1])
      pixel_embedding = mask_input[:, :, 2:]
      self.assertAllClose(pixel_embedding_np, pixel_embedding)

  def test_get_instance_embeddings(self):

    embeddings = np.zeros((32, 32, 2))
    embeddings[8, 8] = 1.0
    embeddings[24, 16] = 2.0
    embeddings = tf.constant(embeddings)

    boxes = tf.constant([[0., 0., 0.5, 0.5], [0.5, 0.0, 1.0, 1.0]])

    center_embeddings = self.model._get_instance_embeddings(boxes, embeddings)

    self.assertAllClose(center_embeddings, [[1.0, 1.0], [2.0, 2.0]])

  def test_get_groundtruth_mask_output(self):

    boxes = tf.constant([[0., 0., 0.25, 0.25], [0.75, 0.75, 1.0, 1.0]],
                        dtype=tf.float32)
    masks = np.zeros((2, 32, 32), dtype=np.float32)
    masks[0, :16, :16] = 0.5
    masks[1, 16:, 16:] = 0.1
    masks = self.model._get_groundtruth_mask_output(boxes, masks)
    self.assertEqual(masks.shape, (2, 16, 16))

    self.assertAllClose(masks[0], np.zeros((16, 16)) + 0.5)
    self.assertAllClose(masks[1], np.zeros((16, 16)) + 0.1)

  def test_get_groundtruth_mask_output_crop_resize(self):

    model = build_meta_arch(predict_full_resolution_masks=True)
    boxes = tf.constant([[0., 0., 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]],
                        dtype=tf.float32)
    masks = tf.ones((2, 32, 32))
    masks = model._get_groundtruth_mask_output(boxes, masks)
    self.assertAllClose(masks, np.ones((2, 32, 32)))

  def test_per_instance_loss(self):

    model = build_meta_arch()
    model._mask_net = MockMaskNet()
    boxes = tf.constant([[0.0, 0.0, 0.25, 0.25], [0.75, 0.75, 1.0, 1.0]])
    masks = np.zeros((2, 32, 32), dtype=np.float32)
    masks[0, :16, :16] = 1.0
    masks[1, 16:, 16:] = 1.0
    masks = tf.constant(masks)

    loss_dict = model._compute_per_instance_deepmac_losses(
        boxes, masks, tf.zeros((32, 32, 2)), tf.zeros((32, 32, 2)),
        tf.zeros((16, 16, 3)))
    self.assertAllClose(
        loss_dict[deepmac_meta_arch.DEEP_MASK_ESTIMATION],
        np.zeros(2) - tf.math.log(tf.nn.sigmoid(0.9)))

  def test_per_instance_loss_no_crop_resize(self):

    model = build_meta_arch(predict_full_resolution_masks=True)
    model._mask_net = MockMaskNet()
    boxes = tf.constant([[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]])
    masks = np.ones((2, 128, 128), dtype=np.float32)
    masks = tf.constant(masks)

    loss_dict = model._compute_per_instance_deepmac_losses(
        boxes, masks, tf.zeros((32, 32, 2)), tf.zeros((32, 32, 2)),
        tf.zeros((32, 32, 3)))
    self.assertAllClose(
        loss_dict[deepmac_meta_arch.DEEP_MASK_ESTIMATION],
        np.zeros(2) - tf.math.log(tf.nn.sigmoid(0.9)))

  def test_per_instance_loss_no_crop_resize_dice(self):

    model = build_meta_arch(predict_full_resolution_masks=True,
                            use_dice_loss=True)
    model._mask_net = MockMaskNet()
    boxes = tf.constant([[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]])
    masks = np.ones((2, 128, 128), dtype=np.float32)
    masks = tf.constant(masks)

    loss_dict = model._compute_per_instance_deepmac_losses(
        boxes, masks, tf.zeros((32, 32, 2)), tf.zeros((32, 32, 2)),
        tf.zeros((32, 32, 3)))
    pred = tf.nn.sigmoid(0.9)
    expected = (1.0 - ((2.0 * pred) / (1.0 + pred)))
    self.assertAllClose(loss_dict[deepmac_meta_arch.DEEP_MASK_ESTIMATION],
                        [expected, expected], rtol=1e-3)

  def test_empty_masks(self):
    boxes = tf.zeros([0, 4])
    masks = tf.zeros([0, 128, 128])

    loss_dict = self.model._compute_per_instance_deepmac_losses(
        boxes, masks, tf.zeros((32, 32, 2)), tf.zeros((32, 32, 2)),
        tf.zeros((16, 16, 3)))
    self.assertEqual(loss_dict[deepmac_meta_arch.DEEP_MASK_ESTIMATION].shape,
                     (0,))

  def test_postprocess(self):

    model = build_meta_arch()
    model._mask_net = MockMaskNet()
    boxes = np.zeros((2, 3, 4), dtype=np.float32)
    boxes[:, :, [0, 2]] = 0.0
    boxes[:, :, [1, 3]] = 8.0
    boxes = tf.constant(boxes)

    masks = model._postprocess_masks(
        boxes, tf.zeros((2, 32, 32, 2)), tf.zeros((2, 32, 32, 2)))
    prob = tf.nn.sigmoid(0.9).numpy()
    self.assertAllClose(masks, prob * np.ones((2, 3, 16, 16)))

  def test_postprocess_emb_proj(self):

    model = build_meta_arch(network_type='embedding_projection',
                            use_instance_embedding=False,
                            use_xy=False, pixel_embedding_dim=8,
                            use_dice_loss=True,
                            dice_loss_prediction_probability=True)
    boxes = np.zeros((2, 3, 4), dtype=np.float32)
    boxes[:, :, [0, 2]] = 0.0
    boxes[:, :, [1, 3]] = 8.0
    boxes = tf.constant(boxes)

    masks = model._postprocess_masks(
        boxes, tf.zeros((2, 32, 32, 2)), tf.zeros((2, 32, 32, 2)))
    self.assertEqual(masks.shape, (2, 3, 16, 16))

  def test_postprocess_emb_proj_fullres(self):

    model = build_meta_arch(network_type='embedding_projection',
                            predict_full_resolution_masks=True,
                            use_instance_embedding=False,
                            pixel_embedding_dim=8, use_xy=False,
                            use_dice_loss=True)
    boxes = np.zeros((2, 3, 4), dtype=np.float32)
    boxes = tf.constant(boxes)

    masks = model._postprocess_masks(
        boxes, tf.zeros((2, 32, 32, 2)), tf.zeros((2, 32, 32, 2)))
    self.assertEqual(masks.shape, (2, 3, 128, 128))

  def test_postprocess_no_crop_resize_shape(self):

    model = build_meta_arch(predict_full_resolution_masks=True)
    model._mask_net = MockMaskNet()
    boxes = np.zeros((2, 3, 4), dtype=np.float32)
    boxes[:, :, [0, 2]] = 0.0
    boxes[:, :, [1, 3]] = 8.0
    boxes = tf.constant(boxes)

    masks = model._postprocess_masks(
        boxes, tf.zeros((2, 32, 32, 2)), tf.zeros((2, 32, 32, 2)))
    prob = tf.nn.sigmoid(0.9).numpy()
    self.assertAllClose(masks, prob * np.ones((2, 3, 128, 128)))

  def test_crop_masks_within_boxes(self):
    masks = np.zeros((2, 32, 32))
    masks[0, :16, :16] = 1.0
    masks[1, 16:, 16:] = 1.0
    boxes = tf.constant([[0.0, 0.0, 15.0 / 32, 15.0 / 32],
                         [0.5, 0.5, 1.0, 1]])
    masks = deepmac_meta_arch.crop_masks_within_boxes(
        masks, boxes, 128)
    masks = (masks.numpy() > 0.0).astype(np.float32)
    self.assertAlmostEqual(masks.sum(), 2 * 128 * 128)

  def test_transform_boxes_to_feature_coordinates(self):
    batch_size = 2
    model = build_meta_arch()
    model._mask_net = MockMaskNet()
    boxes = np.zeros((batch_size, 3, 4), dtype=np.float32)
    boxes[:, :, [0, 2]] = 0.1
    boxes[:, :, [1, 3]] = 0.5
    boxes = tf.constant(boxes)
    true_image_shapes = tf.constant([
        [64, 32, 3],  # Image 1 is padded during resizing.
        [64, 64, 3],  # Image 2 is not padded.
    ])
    resized_image_height = 64
    resized_image_width = 64
    resized_image_shape = [
        batch_size, resized_image_height, resized_image_width, 3
    ]

    feature_map_height = 32
    feature_map_width = 32
    instance_embedding = tf.zeros(
        (batch_size, feature_map_height, feature_map_width, 2))

    expected_boxes = np.array([
        [  # Image 1
            # 0.1 * (64 / resized_image_height) * feature_map_height -> 3.2
            # 0.5 * (32 / resized_image_width) * feature_map_width -> 8.0
            [3.2, 8., 3.2, 8.],
            [3.2, 8., 3.2, 8.],
            [3.2, 8., 3.2, 8.],
        ],
        [  # Image 2
            # 0.1 * (64 / resized_image_height) * feature_map_height -> 3.2
            # 0.5 * (64 / resized_image_width) * feature_map_width -> 16
            [3.2, 16., 3.2, 16.],
            [3.2, 16., 3.2, 16.],
            [3.2, 16., 3.2, 16.],
        ],
    ])

    box_strided = model._transform_boxes_to_feature_coordinates(
        boxes, true_image_shapes, resized_image_shape, instance_embedding)
    self.assertAllClose(box_strided, expected_boxes)

  def test_fc_tf_function(self):

    net = deepmac_meta_arch.MaskHeadNetwork('fully_connected', 8, mask_size=32)
    call_func = tf.function(net.__call__)

    out = call_func(tf.zeros((2, 4)), tf.zeros((2, 32, 32, 8)), training=True)
    self.assertEqual(out.shape, (2, 32, 32))

  def test_box_consistency_loss(self):

    boxes_gt = tf.constant([[0., 0., 0.49, 1.0]])
    boxes_jittered = tf.constant([[0.0, 0.0, 1.0, 1.0]])

    mask_prediction = np.zeros((1, 32, 32)).astype(np.float32)
    mask_prediction[0, :24, :24] = 1.0

    loss = self.model._compute_per_instance_box_consistency_loss(
        boxes_gt, boxes_jittered, tf.constant(mask_prediction))

    yloss = tf.nn.sigmoid_cross_entropy_with_logits(
        labels=tf.constant([1.0] * 8 + [0.0] * 8),
        logits=[1.0] * 12 + [0.0] * 4)
    xloss = tf.nn.sigmoid_cross_entropy_with_logits(
        labels=tf.constant([1.0] * 16),
        logits=[1.0] * 12 + [0.0] * 4)

    self.assertAllClose(loss, [tf.reduce_mean(yloss + xloss).numpy()])

  def test_box_consistency_dice_loss(self):

    model = build_meta_arch(use_dice_loss=True)
    boxes_gt = tf.constant([[0., 0., 0.49, 1.0]])
    boxes_jittered = tf.constant([[0.0, 0.0, 1.0, 1.0]])

    almost_inf = 1e10
    mask_prediction = np.full((1, 32, 32), -almost_inf, dtype=np.float32)
    mask_prediction[0, :24, :24] = almost_inf

    loss = model._compute_per_instance_box_consistency_loss(
        boxes_gt, boxes_jittered, tf.constant(mask_prediction))

    yloss = 1 - 6.0 / 7
    xloss = 0.2

    self.assertAllClose(loss, [yloss + xloss])

  def test_color_consistency_loss_full_res_shape(self):

    model = build_meta_arch(use_dice_loss=True,
                            predict_full_resolution_masks=True)
    boxes = tf.zeros((3, 4))
    img = tf.zeros((32, 32, 3))
    mask_logits = tf.zeros((3, 32, 32))

    loss = model._compute_per_instance_color_consistency_loss(
        boxes, img, mask_logits)
    self.assertEqual([3], loss.shape)

  def test_color_consistency_1_threshold(self):
    model = build_meta_arch(predict_full_resolution_masks=True,
                            color_consistency_threshold=0.99)
    boxes = tf.zeros((3, 4))
    img = tf.zeros((32, 32, 3))
    mask_logits = tf.zeros((3, 32, 32)) - 1e4

    loss = model._compute_per_instance_color_consistency_loss(
        boxes, img, mask_logits)
    self.assertAllClose(loss, np.zeros(3))

  def test_box_consistency_dice_loss_full_res(self):

    model = build_meta_arch(use_dice_loss=True,
                            predict_full_resolution_masks=True)
    boxes_gt = tf.constant([[0., 0., 1.0, 1.0]])
    boxes_jittered = None

    almost_inf = 1e10
    mask_prediction = np.full((1, 32, 32), -almost_inf, dtype=np.float32)
    mask_prediction[0, :16, :32] = almost_inf

    loss = model._compute_per_instance_box_consistency_loss(
        boxes_gt, boxes_jittered, tf.constant(mask_prediction))
    self.assertAlmostEqual(loss[0].numpy(), 1 / 3)

  def test_get_lab_image_shape(self):

    output = self.model._get_lab_image(tf.zeros((2, 4, 4, 3)))
    self.assertEqual(output.shape, (2, 4, 4, 3))

  def test_loss_keys(self):
    model = build_meta_arch(use_dice_loss=True)
    prediction = {
        'preprocessed_inputs': tf.random.normal((1, 32, 32, 3)),
        'INSTANCE_EMBEDDING': [tf.random.normal((1, 8, 8, 17))] * 2,
        'PIXEL_EMBEDDING': [tf.random.normal((1, 8, 8, 19))] * 2,
        'object_center': [tf.random.normal((1, 8, 8, 6))] * 2,
        'box/offset': [tf.random.normal((1, 8, 8, 2))] * 2,
        'box/scale': [tf.random.normal((1, 8, 8, 2))] * 2
    }
    model.provide_groundtruth(
        groundtruth_boxes_list=[tf.convert_to_tensor([[0., 0., 1., 1.]] * 5)],
        groundtruth_classes_list=[tf.one_hot([1, 0, 1, 1, 1], depth=6)],
        groundtruth_weights_list=[tf.ones(5)],
        groundtruth_masks_list=[tf.ones((5, 32, 32))])
    loss = model.loss(prediction, tf.constant([[32, 32, 3.0]]))
    self.assertGreater(loss['Loss/deep_mask_estimation'], 0.0)

    for weak_loss in deepmac_meta_arch.WEAK_LOSSES:
      if weak_loss == deepmac_meta_arch.DEEP_MASK_COLOR_CONSISTENCY:
        continue
      self.assertGreater(loss['Loss/' + weak_loss], 0.0,
                         '{} was <= 0'.format(weak_loss))

  def test_loss_keys_full_res(self):
    model = build_meta_arch(use_dice_loss=True,
                            predict_full_resolution_masks=True)
    prediction = {
        'preprocessed_inputs': tf.random.normal((1, 32, 32, 3)),
        'INSTANCE_EMBEDDING': [tf.random.normal((1, 8, 8, 17))] * 2,
        'PIXEL_EMBEDDING': [tf.random.normal((1, 8, 8, 19))] * 2,
        'object_center': [tf.random.normal((1, 8, 8, 6))] * 2,
        'box/offset': [tf.random.normal((1, 8, 8, 2))] * 2,
        'box/scale': [tf.random.normal((1, 8, 8, 2))] * 2
    }
    model.provide_groundtruth(
        groundtruth_boxes_list=[tf.convert_to_tensor([[0., 0., 1., 1.]] * 5)],
        groundtruth_classes_list=[tf.one_hot([1, 0, 1, 1, 1], depth=6)],
        groundtruth_weights_list=[tf.ones(5)],
        groundtruth_masks_list=[tf.ones((5, 32, 32))])
    loss = model.loss(prediction, tf.constant([[32, 32, 3.0]]))
    self.assertGreater(loss['Loss/deep_mask_estimation'], 0.0)

    for weak_loss in deepmac_meta_arch.WEAK_LOSSES:
      self.assertGreater(loss['Loss/' + weak_loss], 0.0,
                         '{} was <= 0'.format(weak_loss))


@unittest.skipIf(tf_version.is_tf1(), 'Skipping TF2.X only test.')
class FullyConnectedMaskHeadTest(tf.test.TestCase):

  def test_fc_mask_head(self):
    head = deepmac_meta_arch.FullyConnectedMaskHead(512, 16)
    inputs = tf.random.uniform([100, 16, 16, 512])
    output = head(inputs)
    self.assertAllEqual([100, 16, 16, 1], output.numpy().shape)


@unittest.skipIf(tf_version.is_tf1(), 'Skipping TF2.X only test.')
class ResNetMaskHeadTest(tf.test.TestCase, parameterized.TestCase):

  @parameterized.parameters(['resnet4', 'resnet8', 'resnet20'])
  def test_forward(self, name):
    net = deepmac_meta_arch.ResNetMaskNetwork(name, 8)
    out = net(tf.zeros((3, 32, 32, 16)))
    self.assertEqual(out.shape[:3], (3, 32, 32))


if __name__ == '__main__':
  tf.test.main()
