# -*- coding: utf-8 -*-
"""
.. _tut-artifact-overview:

Overview of artifact detection
==============================

This tutorial covers the basics of artifact detection, and introduces the
artifact detection tools available in MNE-Python.

.. contents:: Page contents
   :local:
   :depth: 2

We begin as always by importing the necessary Python modules and loading some
:ref:`example data <sample-dataset>`:
"""

import os
import numpy as np
import mne

sample_data_folder = mne.datasets.sample.data_path()
sample_data_raw_file = os.path.join(sample_data_folder, 'MEG', 'sample',
                                    'sample_audvis_raw.fif')
raw = mne.io.read_raw_fif(sample_data_raw_file)
raw.crop(0, 60).load_data()  # just use a fraction of data for speed here

###############################################################################
# What are artifacts?
# ^^^^^^^^^^^^^^^^^^^
#
# Artifacts are parts of the recorded signal that arise from sources other than
# the source of interest (i.e., neuronal activity in the brain). As such,
# artifacts are a form of interference or noise relative to the signal of
# interest. There are many possible causes of such interference, for example:
#
# - Environmental artifacts
#     - Persistent oscillations centered around the `AC power line frequency`_
#       (typically 50 or 60 Hz)
#     - Brief signal jumps due to building vibration (such as a door slamming)
#     - Electromagnetic field noise from nearby elevators, cell phones, the
#       geomagnetic field, etc.
#
# - Instrumentation artifacts
#     - Electromagnetic interference from stimulus presentation (such as EEG
#       sensors picking up the field generated by unshielded headphones)
#     - Continuous oscillations at specific frequencies used by head position
#       indicator (HPI) coils
#     - Random high-amplitude fluctuations (or alternatively, constant zero
#       signal) in a single channel due to sensor malfunction (e.g., in surface
#       electrodes, poor scalp contact)
#
# - Biological artifacts
#     - Periodic `QRS`_-like signal patterns (especially in magnetometer
#       channels) due to electrical activity of the heart
#     - Short step-like deflections (especially in frontal EEG channels) due to
#       eye movements
#     - Large transient deflections (especially in frontal EEG channels) due to
#       blinking
#     - Brief bursts of high frequency fluctuations across several channels due
#       to the muscular activity during swallowing
#
# There are also some cases where signals from within the brain can be
# considered artifactual. For example, if a researcher is primarily interested
# in the sensory response to a stimulus, but the experimental paradigm involves
# a behavioral response (such as button press), the neural activity associated
# with the planning and executing the button press could be considered an
# artifact relative to signal of interest (i.e., the evoked sensory response).
#
# .. note::
#     Artifacts of the same genesis may appear different in recordings made by
#     different EEG or MEG systems, due to differences in sensor design (e.g.,
#     passive vs. active EEG electrodes; axial vs. planar gradiometers, etc).
#
#
# What to do about artifacts
# ^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# There are 3 basic options when faced with artifacts in your recordings:
#
# 1. *Ignore* the artifact and carry on with analysis
# 2. *Exclude* the corrupted portion of the data and analyze the remaining data
# 3. *Repair* the artifact by suppressing artifactual part of the recording
#    while (hopefully) leaving the signal of interest intact
#
# There are many different approaches to repairing artifacts, and MNE-Python
# includes a variety of tools for artifact repair, including digital filtering,
# independent components analysis (ICA), Maxwell filtering / signal-space
# separation (SSS), and signal-space projection (SSP). Separate tutorials
# demonstrate each of these techniques for artifact repair. Many of the
# artifact repair techniques work on both continuous (raw) data and on data
# that has already been epoched (though not necessarily equally well); some can
# be applied to `memory-mapped`_ data while others require the data to be
# copied into RAM. Of course, before you can choose any of these strategies you
# must first *detect* the artifacts, which is the topic of the next section.
#
#
# Artifact detection
# ^^^^^^^^^^^^^^^^^^
#
# MNE-Python includes a few tools for automated detection of certain artifacts
# (such as heartbeats and blinks), but of course you can always visually
# inspect your data to identify and annotate artifacts as well.
#
# We saw in :ref:`the introductory tutorial <tut-overview>` that the example
# data includes :term:`SSP projectors <projector>`, so before we look at
# artifacts let's set aside the projectors in a separate variable and then
# remove them from the :class:`~mne.io.Raw` object using the
# :meth:`~mne.io.Raw.del_proj` method, so that we can inspect our data in it's
# original, raw state:

ssp_projectors = raw.info['projs']
raw.del_proj()

###############################################################################
# Low-frequency drifts
# ~~~~~~~~~~~~~~~~~~~~
#
# Low-frequency drifts are most readily detected by visual inspection using the
# basic :meth:`~mne.io.Raw.plot` method, though it is helpful to plot a
# relatively long time span and to disable channel-wise DC shift correction.
# Here we plot 60 seconds and show all the magnetometer channels:

mag_channels = mne.pick_types(raw.info, meg='mag')
raw.plot(duration=60, order=mag_channels, n_channels=len(mag_channels),
         remove_dc=False)

###############################################################################
# Low-frequency drifts are readily removed by high-pass filtering at a fairly
# low cutoff frequency (the wavelength of the drifts seen above is probably
# around 20 seconds, so in this case a cutoff of 0.1 Hz would probably suppress
# most of the drift).
#
#
# Power line noise
# ~~~~~~~~~~~~~~~~
#
# Power line artifacts are easiest to see on plots of the spectrum, so we'll
# use :meth:`~mne.io.Raw.plot_psd` to illustrate.

fig = raw.plot_psd(tmax=np.inf, fmax=250, average=True)
# add some arrows at 60 Hz and its harmonics:
for ax in fig.axes[1:]:
    freqs = ax.lines[-1].get_xdata()
    psds = ax.lines[-1].get_ydata()
    for freq in (60, 120, 180, 240):
        idx = np.searchsorted(freqs, freq)
        ax.arrow(x=freqs[idx], y=psds[idx] + 18, dx=0, dy=-12, color='red',
                 width=0.1, head_width=3, length_includes_head=True)


###############################################################################
# Here we see narrow frequency peaks at 60, 120, 180, and 240 Hz — the power
# line frequency of the USA (where the sample data was recorded) and its 2nd,
# 3rd, and 4th harmonics. Other peaks (around 25 to 30 Hz, and the second
# harmonic of those) are probably related to the heartbeat, which is more
# easily seen in the time domain using a dedicated heartbeat detection function
# as described in the next section.
#
#
# Heartbeat artifacts (ECG)
# ~~~~~~~~~~~~~~~~~~~~~~~~~
#
# MNE-Python includes a dedicated function
# :func:`~mne.preprocessing.find_ecg_events` in the :mod:`mne.preprocessing`
# submodule, for detecting heartbeat artifacts from either dedicated ECG
# channels or from magnetometers (if no ECG channel is present). Additionally,
# the function :func:`~mne.preprocessing.create_ecg_epochs` will call
# :func:`~mne.preprocessing.find_ecg_events` under the hood, and use the
# resulting events array to extract epochs centered around the detected
# heartbeat artifacts. Lastly, :func:`~mne.preprocessing.annotate_ecg` can be
# used to annotate ECG activity in raw data.
#
# Here we will first create and display those `~mne.Annotations`, before
# producing ECG epochs and then showing an image plot of
# the detected ECG artifacts along with the average ERF across artifacts. We'll
# show all three channel types, even though EEG channels are less strongly
# affected by heartbeat artifacts:
#
# Let's start with the `~mne.Annotations`. We can create them by calling
# :func:`~mne.preprocessing.annotate_ecg`; then we attach them to a copy of
# our raw data (because we don't want to modify the original), and create a
# plot.

ecg_annotations = mne.preprocessing.annotate_ecg(raw)
raw.copy().set_annotations(ecg_annotations).plot()

###############################################################################
# This has annotated all heart beats for their **entire** duration. If you'd
# rather just mark the **peaks** of the ECG R component, pass
# ``what='r-peaks'``:

ecg_annotations = mne.preprocessing.annotate_ecg(raw, what='r-peaks')
raw.copy().set_annotations(ecg_annotations).plot()

###############################################################################
# Under the hood, :func:`~mne.preprocessing.annotate_ecg` calls
# :func:`~mne.preprocessing.find_ecg_events` and simply turns the returned
# events into `~mne.Annotations`. Let's call this function directly and
# visualize the ECG events on the raw data. By default, the function assigns
# the event number ``999`` to ECG events. The resulting figure should look
# almost identical to the one we just produced using
# `~mne.preprocessing.annotate_ecg`.

events, ecg_ch, average_hr = mne.preprocessing.find_ecg_events(raw)
raw.plot(events=events, event_id={999: 'ECG R Peak'})
print(f'Found {len(events)} ECG events in channel {ecg_ch}. The average heart '
      f'rate was: {average_hr} beats per minute.')

###############################################################################
# Finally, let us create epochs centered around the ECG R wave peaks.

# sphinx_gallery_thumbnail_number = 4
ecg_epochs = mne.preprocessing.create_ecg_epochs(raw)
ecg_epochs.plot_image(combine='mean')

###############################################################################
# The horizontal streaks in the magnetometer image plot reflect the fact that
# the heartbeat artifacts are superimposed on low-frequency drifts like the one
# we saw in an earlier section; to avoid this you could pass
# ``baseline=(-0.5, -0.2)`` in the call to
# :func:`~mne.preprocessing.create_ecg_epochs`.
# You can also get a quick look at the
# ECG-related field pattern across sensors by averaging the ECG epochs together
# via the :meth:`~mne.Epochs.average` method, and then using the
# :meth:`mne.Evoked.plot_topomap` method:

avg_ecg_epochs = ecg_epochs.average().apply_baseline((-0.5, -0.2))

###############################################################################
# Here again we can visualize the spatial pattern of the associated field at
# various times relative to the peak of the EOG response:

avg_ecg_epochs.plot_topomap(times=np.linspace(-0.05, 0.05, 11))

###############################################################################
# Or, we can get an ERP/F plot with :meth:`~mne.Evoked.plot` or a combined
# scalp field maps and ERP/F plot with :meth:`~mne.Evoked.plot_joint`. Here
# we've specified the times for scalp field maps manually, but if not provided
# they will be chosen automatically based on peaks in the signal:

avg_ecg_epochs.plot_joint(times=[-0.25, -0.025, 0, 0.025, 0.25])

###############################################################################
# Ocular artifacts (EOG)
# ~~~~~~~~~~~~~~~~~~~~~~
#
# Similar to the ECG detection and epoching methods described above, MNE-Python
# also includes functions for detecting and extracting ocular artifacts:
# :func:`~mne.preprocessing.find_eog_events` and
# :func:`~mne.preprocessing.create_eog_epochs`. Once again we'll use the
# higher-level convenience function that automatically finds the artifacts and
# extracts them in to an :class:`~mne.Epochs` object in one step. Unlike the
# heartbeat artifacts seen above, ocular artifacts are usually most prominent
# in the EEG channels, but we'll still show all three channel types. We'll use
# the ``baseline`` parameter this time too; note that there are many fewer
# blinks than heartbeats, which makes the image plots appear somewhat blocky:

eog_epochs = mne.preprocessing.create_eog_epochs(raw, baseline=(-0.5, -0.2))
eog_epochs.plot_image(combine='mean')
eog_epochs.average().plot_joint()

###############################################################################
# Summary
# ^^^^^^^
#
# Familiarizing yourself with typical artifact patterns and magnitudes is a
# crucial first step in assessing the efficacy of later attempts to repair
# those artifacts. A good rule of thumb is that the artifact amplitudes should
# be orders of magnitude larger than your signal of interest — and there should
# be several occurrences of such events — in order to find signal
# decompositions that effectively estimate and repair the artifacts.
#
# Several other tutorials in this section illustrate the various tools for
# artifact repair, and discuss the pros and cons of each technique, for
# example:
#
# - :ref:`tut-artifact-ssp`
# - :ref:`tut-artifact-ica`
# - :ref:`tut-artifact-sss`
#
# There are also tutorials on general-purpose preprocessing steps such as
# :ref:`filtering and resampling <tut-filter-resample>` and :ref:`excluding
# bad channels <tut-bad-channels>` or :ref:`spans of data
# <tut-reject-data-spans>`.
#
# .. LINKS
#
# .. _`AC power line frequency`:
#    https://en.wikipedia.org/wiki/Mains_electricity
# .. _`QRS`: https://en.wikipedia.org/wiki/QRS_complex
# .. _`memory-mapped`: https://en.wikipedia.org/wiki/Memory-mapped_file
