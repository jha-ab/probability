# Lint as: python3
# Copyright 2020 The TensorFlow Probability Authors.
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
"""Brownian Motion model."""

import functools

import tensorflow.compat.v2 as tf
import tensorflow_probability as tfp

from inference_gym.internal import data
from inference_gym.targets import bayesian_model
from inference_gym.targets import model
from inference_gym.targets.ground_truth import brownian_motion_missing_middle_observations
from inference_gym.targets.ground_truth import brownian_motion_unknown_scales_missing_middle_observations

tfb = tfp.bijectors
tfd = tfp.distributions

__all__ = [
    'BrownianMotion',
    'BrownianMotionMissingMiddleObservations',
    'BrownianMotionUnknownScales',
    'BrownianMotionUnknownScalesMissingMiddleObservations',
]

Root = tfd.JointDistributionCoroutine.Root


def brownian_motion_prior_fn(num_timesteps, innovation_noise_scale):
  """Generative process for the Brownian Motion model."""
  prior_loc = 0.
  new = yield Root(tfd.Normal(loc=prior_loc,
                              scale=innovation_noise_scale,
                              name='x_0'))
  for t in range(1, num_timesteps):
    new = yield tfd.Normal(loc=new,
                           scale=innovation_noise_scale,
                           name='x_{}'.format(t))


def brownian_motion_unknown_scales_prior_fn(num_timesteps):
  """Generative process for the Brownian Motion model with unknown scales."""
  innovation_noise_scale = yield Root(tfd.LogNormal(
      0., 2., name='innovation_noise_scale'))
  _ = yield Root(tfd.LogNormal(0., 2., name='observation_noise_scale'))
  yield from brownian_motion_prior_fn(
      num_timesteps,
      innovation_noise_scale=innovation_noise_scale)


def brownian_motion_log_likelihood_fn(values,
                                      locs,
                                      observation_noise_scale=None):
  """Likelihood of observed data under the Brownian Motion model."""
  if observation_noise_scale is None:
    (_, observation_noise_scale), values = values[:2], values[2:]
  observation_noise_scale = tf.convert_to_tensor(
      observation_noise_scale, name='observation_noise_scale')
  latents = tf.stack(values, axis=-1)
  lps = tfd.Normal(loc=latents,
                   scale=observation_noise_scale[..., tf.newaxis]
                   ).log_prob(tf.where(tf.math.is_finite(locs), locs, 0.))
  return tf.reduce_sum(tf.where(tf.math.is_finite(locs), lps, 0.), axis=-1)


class BrownianMotion(bayesian_model.BayesianModel):
  """Construct the Brownian Motion model.

  This models a Brownian Motion process. Each timestep consists of a Normal
  distribution with a `loc` parameter. If there are no observations from a given
  timestep, the loc value is np.nan. The constants `innovation_noise` and
  `observation noise` are shared across all timesteps.

  ```none
  # The actual value of the loc parameter at timestep t is:
  loc_{t+1} | loc_{t} ~ Normal(loc_t, innovation_noise)

  # The observed loc at each timestep t (which make up the locs array) is:
  observed_loc_{t} ~ Normal(loc_{t}, observation_noise)
  ```
  """

  def __init__(self,
               locs,
               innovation_noise_scale,
               observation_noise_scale,
               name='brownian_motion',
               pretty_name='Brownian Motion'):
    """Construct the Brownian Motion model.

    Args:
      locs: Array of loc parameters with nan value if loc is unobserved.
      innovation_noise_scale: Python `float`.
      observation_noise_scale: Python `float`.
      name: Python `str` name prefixed to Ops created by this class.
      pretty_name: A Python `str`. The pretty name of this model.
    """
    with tf.name_scope(name):
      num_timesteps = locs.shape[0]
      self._prior_dist = tfd.JointDistributionCoroutine(
          functools.partial(
              brownian_motion_prior_fn,
              num_timesteps=num_timesteps,
              innovation_noise_scale=innovation_noise_scale))

      self._log_likelihood_fn = functools.partial(
          brownian_motion_log_likelihood_fn,
          observation_noise_scale=observation_noise_scale,
          locs=locs)

      def _ext_identity(params):
        return tf.stack(params, axis=-1)

      sample_transformations = {
          'identity':
              model.Model.SampleTransformation(
                  fn=_ext_identity,
                  pretty_name='Identity',
              )
      }

    event_space_bijector = type(
        self._prior_dist.dtype)(*([tfb.Identity()] * num_timesteps))
    super(BrownianMotion, self).__init__(
        default_event_space_bijector=event_space_bijector,
        event_shape=self._prior_dist.event_shape,
        dtype=self._prior_dist.dtype,
        name=name,
        pretty_name=pretty_name,
        sample_transformations=sample_transformations,
    )

  def _prior_distribution(self):
    return self._prior_dist

  def log_likelihood(self, value):
    return self._log_likelihood_fn(value)


class BrownianMotionMissingMiddleObservations(BrownianMotion):
  """A simple Brownian Motion with 30 timesteps where 10 are unobservable."""

  GROUND_TRUTH_MODULE = brownian_motion_missing_middle_observations

  def __init__(self):
    dataset = data.brownian_motion_missing_middle_observations()
    super(BrownianMotionMissingMiddleObservations, self).__init__(
        name='brownian_motion_missing_middle_observations',
        pretty_name='Brownian Motion Missing Middle Observations',
        **dataset)


class BrownianMotionUnknownScales(bayesian_model.BayesianModel):
  """Construct the Brownian Motion model with unknown scale parameters.

  This models a Brownian Motion process. Each timestep consists of a Normal
  distribution with a `loc` parameter. If there are no observations from a given
  timestep, the loc value is np.nan. The unknown `innovation_noise_scale` and
  `observation_noise_scale` are shared across all timesteps.

  ```none
  innovation_noise_scale ~ LogNormal(0., 2.)
  observation_noise_scale ~ LogNormal(0., 2.)

  # The actual value of the loc parameter at timestep t is:
  loc_{t+1} | loc_{t} ~ Normal(loc_t, innovation_noise_scale)

  # The observed loc at each timestep t (which make up the locs array) is:
  observed_loc_{t} ~ Normal(loc_{t}, observation_noise_scale)
  ```
  """

  def __init__(self,
               locs,
               name='brownian_motion_unknown_scales',
               pretty_name='Brownian Motion with Unknown Scales'):
    """Construct the Brownian Motion model with unknown scales.

    Args:
      locs: Array of loc parameters with nan value if loc is unobserved.
      name: Python `str` name prefixed to Ops created by this class.
      pretty_name: A Python `str`. The pretty name of this model.
    """
    with tf.name_scope(name):
      num_timesteps = locs.shape[0]
      self._prior_dist = tfd.JointDistributionCoroutine(
          functools.partial(
              brownian_motion_unknown_scales_prior_fn,
              num_timesteps=num_timesteps))

      self._log_likelihood_fn = functools.partial(
          brownian_motion_log_likelihood_fn,
          locs=locs)

      def _ext_identity(params):
        return {'innovation_noise_scale': params[0],
                'observation_noise_scale': params[1],
                'locs': tf.stack(params[2:], axis=-1)}

      sample_transformations = {
          'identity':
              model.Model.SampleTransformation(
                  fn=_ext_identity,
                  pretty_name='Identity',
                  dtype={'innovation_noise_scale': tf.float32,
                         'observation_noise_scale': tf.float32,
                         'locs': tf.float32})
      }

    event_space_bijector = type(
        self._prior_dist.dtype)(*(
            [tfb.Softplus(),
             tfb.Softplus()
             ] + [tfb.Identity()] * num_timesteps))
    super(BrownianMotionUnknownScales, self).__init__(
        default_event_space_bijector=event_space_bijector,
        event_shape=self._prior_dist.event_shape,
        dtype=self._prior_dist.dtype,
        name=name,
        pretty_name=pretty_name,
        sample_transformations=sample_transformations,
    )

  def _prior_distribution(self):
    return self._prior_dist

  def log_likelihood(self, value):
    return self._log_likelihood_fn(value)


class BrownianMotionUnknownScalesMissingMiddleObservations(
    BrownianMotionUnknownScales):
  """A simple Brownian Motion with 30 timesteps where 10 are unobservable."""

  GROUND_TRUTH_MODULE = (
      brownian_motion_unknown_scales_missing_middle_observations)

  def __init__(self):
    dataset = data.brownian_motion_missing_middle_observations()
    del dataset['innovation_noise_scale']
    del dataset['observation_noise_scale']
    super(BrownianMotionUnknownScalesMissingMiddleObservations, self).__init__(
        name='brownian_motion_unknown_scales_missing_middle_observations',
        pretty_name='Brownian Motion with Unknown Scales',
        **dataset)
