# Authors: Alexandre Gramfort <alexandre.gramfort@inria.fr>
#          Denis Engemann <denis.engemann@gmail.com>
#          Eric Larson <larson.eric.d@gmail.com>
#
# License: BSD (3-clause)

import numpy as np

from ..annotations import (_annotations_starts_stops, Annotations,
                           annotations_from_events)
from ..utils import logger, verbose, sum_squared, warn, _check_option
from ..filter import filter_data
from ..epochs import Epochs, BaseEpochs
from ..io.base import BaseRaw
from ..evoked import Evoked
from ..io import RawArray
from ..io.meas_info import create_info
from ..io.pick import _picks_to_idx, pick_types, pick_channels


@verbose
def qrs_detector(sfreq, ecg, thresh_value=0.6, levels=2.5, n_thresh=3,
                 l_freq=5, h_freq=35, tstart=0, filter_length='10s',
                 verbose=None):
    """Detect QRS component in ECG channels.

    QRS is the main wave on the heart beat.

    Parameters
    ----------
    sfreq : float
        Sampling rate
    ecg : array
        ECG signal
    thresh_value : float | str
        qrs detection threshold. Can also be "auto" for automatic
        selection of threshold.
    levels : float
        number of std from mean to include for detection
    n_thresh : int
        max number of crossings
    l_freq : float
        Low pass frequency
    h_freq : float
        High pass frequency
    tstart : float
        Start detection after tstart seconds.
    filter_length : str | int | None
        Number of taps to use for filtering.
    %(verbose)s

    Returns
    -------
    events : array
        Indices of ECG peaks
    """
    win_size = int(round((60.0 * sfreq) / 120.0))

    filtecg = filter_data(ecg, sfreq, l_freq, h_freq, None, filter_length,
                          0.5, 0.5, phase='zero-double', fir_window='hann',
                          fir_design='firwin2')

    ecg_abs = np.abs(filtecg)
    init = int(sfreq)

    n_samples_start = int(sfreq * tstart)
    ecg_abs = ecg_abs[n_samples_start:]

    n_points = len(ecg_abs)

    maxpt = np.empty(3)
    maxpt[0] = np.max(ecg_abs[:init])
    maxpt[1] = np.max(ecg_abs[init:init * 2])
    maxpt[2] = np.max(ecg_abs[init * 2:init * 3])

    init_max = np.mean(maxpt)

    if thresh_value == 'auto':
        thresh_runs = np.arange(0.3, 1.1, 0.05)
    elif isinstance(thresh_value, str):
        raise ValueError('threshold value must be "auto" or a float')
    else:
        thresh_runs = [thresh_value]

    # Try a few thresholds (or just one)
    clean_events = list()
    for thresh_value in thresh_runs:
        thresh1 = init_max * thresh_value
        numcross = list()
        time = list()
        rms = list()
        ii = 0
        while ii < (n_points - win_size):
            window = ecg_abs[ii:ii + win_size]
            if window[0] > thresh1:
                max_time = np.argmax(window)
                time.append(ii + max_time)
                nx = np.sum(np.diff(((window > thresh1).astype(np.int64) ==
                                     1).astype(int)))
                numcross.append(nx)
                rms.append(np.sqrt(sum_squared(window) / window.size))
                ii += win_size
            else:
                ii += 1

        if len(rms) == 0:
            rms.append(0.0)
            time.append(0.0)
        time = np.array(time)
        rms_mean = np.mean(rms)
        rms_std = np.std(rms)
        rms_thresh = rms_mean + (rms_std * levels)
        b = np.where(rms < rms_thresh)[0]
        a = np.array(numcross)[b]
        ce = time[b[a < n_thresh]]

        ce += n_samples_start
        clean_events.append(ce)

    # pick the best threshold; first get effective heart rates
    rates = np.array([60. * len(cev) / (len(ecg) / float(sfreq))
                      for cev in clean_events])

    # now find heart rates that seem reasonable (infant through adult athlete)
    idx = np.where(np.logical_and(rates <= 160., rates >= 40.))[0]
    if len(idx) > 0:
        ideal_rate = np.median(rates[idx])  # get close to the median
    else:
        ideal_rate = 80.  # get close to a reasonable default
    idx = np.argmin(np.abs(rates - ideal_rate))
    clean_events = clean_events[idx]
    return clean_events


@verbose
def find_ecg_events(raw, event_id=999, ch_name=None, tstart=0.0,
                    l_freq=5, h_freq=35, qrs_threshold='auto',
                    filter_length='10s', return_ecg=False,
                    reject_by_annotation=True, verbose=None):
    """Find ECG peaks.

    Parameters
    ----------
    raw : instance of Raw
        The raw data.
    %(ecg_event_id)s
    %(ecg_ch_name)s
    %(ecg_tstart)s
    %(ecg_filter_freqs)s
    %(ecg_qrs_threshold)s
    %(ecg_filter_length)s
    return_ecg : bool
        Return the ECG channel if it was synthesized. Defaults to ``False``.
        If ``True`` and an actual ECG channel was found in the data, this will
        return ``None``.
    %(reject_by_annotation_all)s

        .. versionadded:: 0.18
    %(verbose)s

    Returns
    -------
    ecg_events : array
        Events.
    ch_ecg : string
        Name of channel used.
    average_pulse : float
        Estimated average pulse.
    ecg : array | None
        The ECG data of the synthesized ECG channel, if any. This will only
        be returned if ``return_ecg=True`` was passed.

    See Also
    --------
    create_ecg_epochs
    compute_proj_ecg
    """
    skip_by_annotation = ('edge', 'bad') if reject_by_annotation else ()
    del reject_by_annotation
    idx_ecg = _get_ecg_channel_index(ch_name, raw)
    if idx_ecg is not None:
        logger.info('Using channel %s to identify heart beats.'
                    % raw.ch_names[idx_ecg])
        ecg = raw.get_data(picks=idx_ecg)
    else:
        ecg = _make_ecg(raw, None, None)[0]
    assert ecg.ndim == 2 and ecg.shape[0] == 1
    ecg = ecg[0]
    # Deal with filtering the same way we do in raw, i.e. filter each good
    # segment
    onsets, ends = _annotations_starts_stops(
        raw, skip_by_annotation, 'reject_by_annotation', invert=True)
    ecgs = list()
    max_idx = (ends - onsets).argmax()
    for si, (start, stop) in enumerate(zip(onsets, ends)):
        # Only output filter params once (for info level), and only warn
        # once about the length criterion (longest segment is too short)
        use_verbose = verbose if si == max_idx else 'error'
        ecgs.append(filter_data(
            ecg[start:stop], raw.info['sfreq'], l_freq, h_freq, [0],
            filter_length, 0.5, 0.5, 1, 'fir', None, copy=False,
            phase='zero-double', fir_window='hann', fir_design='firwin2',
            verbose=use_verbose))
    ecg = np.concatenate(ecgs)

    # detecting QRS and generating event file. Since not user-controlled, don't
    # output filter params here (hardcode verbose=False)
    ecg_events = qrs_detector(raw.info['sfreq'], ecg, tstart=tstart,
                              thresh_value=qrs_threshold, l_freq=None,
                              h_freq=None, verbose=False)

    # map ECG events back to original times
    remap = np.empty(len(ecg), int)
    offset = 0
    for start, stop in zip(onsets, ends):
        this_len = stop - start
        assert this_len >= 0
        remap[offset:offset + this_len] = np.arange(start, stop)
        offset += this_len
    assert offset == len(ecg)
    ecg_events = remap[ecg_events]

    n_events = len(ecg_events)
    duration_sec = len(ecg) / raw.info['sfreq'] - tstart
    duration_min = duration_sec / 60.
    average_pulse = n_events / duration_min
    logger.info("Number of ECG events detected : %d (average pulse %d / "
                "min.)" % (n_events, average_pulse))

    ecg_events = np.array([ecg_events + raw.first_samp,
                           np.zeros(n_events, int),
                           event_id * np.ones(n_events, int)]).T
    out = (ecg_events, idx_ecg, average_pulse)
    ecg = ecg[np.newaxis]  # backward compat output 2D
    if return_ecg:
        out += (ecg,)
    return out


def _ecg_segment_window(heart_rate):
    """Create a suitable time window around an ECG R wave peak.

    This ideally creates a window that contains the entire heartbeat.

    Parameters
    ----------
    heart_rate : float
        The (average) heart rate.

    Returns
    -------
    epochs_start
        Start of the heartbeat, relative to the R peak, in seconds.
    epochs_end
        End of the heartbeat, relative to the R peak, in seconds.
    """
    # Create a window around the R wave peaks. This algorithm was copied
    # from NeuroKit2 (MIT license). Original code:
    # https://github.com/neuropsychology/NeuroKit/blob/9ec5674b963cdc96709105a01a8af5c2c18c1ee5/neurokit2/ecg/ecg_segment.py#L79-L100  # noqa

    # Modulator
    m = heart_rate / 60

    # Window
    epochs_start = -0.35 / m
    epochs_end = 0.5 / m

    # Adjust for high heart rates
    if heart_rate >= 80:
        c = 0.1
        epochs_start = epochs_start - c
        epochs_end = epochs_end + c

    return epochs_start, epochs_end


@verbose
def annotate_ecg(raw, what='heartbeats', ch_name=None,
                 tstart=0.0, l_freq=5, h_freq=35,
                 qrs_threshold='auto', filter_length='10s',
                 reject_by_annotation=True, verbose=None):
    """Annotate entire heartbeats or the peak of the R wave of an ECG signal.

    .. versionadded:: 0.22

    Parameters
    ----------
    raw : instance of Raw
        The raw data to analyze.
    what : 'heartbeats' | 'r-peaks'
        Whether to annotate entire heartbeats or only the peaks of the R waves.
        R wave peak annotations will have a duration of zero. When annotating
        heartbeats, a window will be created around the R peak; the window
        size will be determined by the average heart rate.

        .. note::
            This function uses :func:`mne.preprocessing.find_ecg_events` to
            locate the peaks of the R waves. Onset and duration of heartbeat
            annotations are based on R peaks and the average heart rate. Note
            that heartbeat annotations are just rough estimates, as no P and T
            components are actually identified.
    %(ecg_ch_name)s
    %(ecg_tstart)s
    %(ecg_filter_freqs)s
    %(ecg_qrs_threshold)s
    %(ecg_filter_length)s
    %(reject_by_annotation_all)s
    %(verbose)s

    Returns
    -------
    annotations : mne.Annotations
        The generated `~mne.Annotations`. If no ECG activity could be
        discovered, returns an empty set of `~mne.Annotations`.
    """
    _check_option('what', what, ['heartbeats', 'r-peaks'])

    logger.info('Detecting R wave peaks.')
    event_id = 999
    ecg_events, _, hr_avg = find_ecg_events(
        raw=raw, ch_name=ch_name, tstart=tstart, event_id=event_id,
        l_freq=l_freq, h_freq=h_freq, filter_length=filter_length,
        qrs_threshold=qrs_threshold,
        reject_by_annotation=reject_by_annotation,
        verbose=verbose)

    if ecg_events.size == 0:
        logger.info('No ECG activity found.')
        return Annotations(onset=[], duration=[], description=[],
                           orig_time=raw.annotations.orig_time)
    annotations = annotations_from_events(
        events=ecg_events, sfreq=raw.info['sfreq'],
        orig_time=raw.annotations.orig_time,
        event_desc={event_id: 'ECG/R peak'})

    if what == 'r-peaks':
        return annotations

    if what == 'heartbeats':
        heartbeat_onset, heartbeat_offset = _ecg_segment_window(hr_avg)
        heartbeat_duration = heartbeat_offset - heartbeat_onset

        annotations.onset += heartbeat_onset
        # Ensure we won't end up with negative times here
        annotations.onset[annotations.onset < 0] = 0

        annotations.duration.fill(heartbeat_duration)
        annotations.description.fill('ECG/Heartbeat')
        return annotations


def _get_ecg_channel_index(ch_name, inst):
    """Get ECG channel index, if no channel found returns None."""
    if ch_name is None:
        ecg_idx = pick_types(inst.info, meg=False, eeg=False, stim=False,
                             eog=False, ecg=True, emg=False, ref_meg=False,
                             exclude='bads')
    else:
        if ch_name not in inst.ch_names:
            raise ValueError('%s not in channel list (%s)' %
                             (ch_name, inst.ch_names))
        ecg_idx = pick_channels(inst.ch_names, include=[ch_name])

    if len(ecg_idx) == 0:
        return None
        # raise RuntimeError('No ECG channel found. Please specify ch_name '
        #                    'parameter e.g. MEG 1531')

    if len(ecg_idx) > 1:
        warn('More than one ECG channel found. Using only %s.'
             % inst.ch_names[ecg_idx[0]])

    return ecg_idx[0]


@verbose
def create_ecg_epochs(raw, ch_name=None, event_id=999, picks=None, tmin=-0.5,
                      tmax=0.5, l_freq=8, h_freq=16, reject=None, flat=None,
                      baseline=None, preload=True, keep_ecg=False,
                      reject_by_annotation=True, decim=1, verbose=None):
    """Conveniently generate epochs around ECG artifact events.

    Parameters
    ----------
    raw : instance of Raw
        The raw data.
    %(ecg_ch_name)s
    %(ecg_event_id)s
    %(picks_all)s
    tmin : float
        Start time before event.
    tmax : float
        End time after event.
    %(ecg_filter_freqs)s
    %(reject_epochs)s
    flat : dict | None
        Rejection parameters based on flatness of signal.
        Valid keys are 'grad' | 'mag' | 'eeg' | 'eog' | 'ecg', and values
        are floats that set the minimum acceptable peak-to-peak amplitude.
        If flat is None then no rejection is done.
    %(baseline_epochs)s
    preload : bool
        Preload epochs or not (default True). Must be True if
        keep_ecg is True.
    keep_ecg : bool
        When ECG is synthetically created (after picking), should it be added
        to the epochs? Must be False when synthetic channel is not used.
        Defaults to False.
    %(reject_by_annotation_epochs)s

        .. versionadded:: 0.14.0
    %(decim)s

        .. versionadded:: 0.21.0
    %(verbose)s

    Returns
    -------
    ecg_epochs : instance of Epochs
        Data epoched around ECG r-peaks.

    See Also
    --------
    find_ecg_events
    compute_proj_ecg

    Notes
    -----
    Filtering is only applied to the ECG channel while finding events.
    The resulting ``ecg_epochs`` will have no filtering applied (i.e., have
    the same filter properties as the input ``raw`` instance).
    """
    has_ecg = 'ecg' in raw or ch_name is not None
    if keep_ecg and (has_ecg or not preload):
        raise ValueError('keep_ecg can be True only if the ECG channel is '
                         'created synthetically and preload=True.')

    events, _, _, ecg = find_ecg_events(
        raw, ch_name=ch_name, event_id=event_id, l_freq=l_freq, h_freq=h_freq,
        return_ecg=True, reject_by_annotation=reject_by_annotation)

    picks = _picks_to_idx(raw.info, picks, 'all', exclude=())

    # create epochs around ECG events and baseline (important)
    ecg_epochs = Epochs(raw, events=events, event_id=event_id,
                        tmin=tmin, tmax=tmax, proj=False, flat=flat,
                        picks=picks, reject=reject, baseline=baseline,
                        reject_by_annotation=reject_by_annotation,
                        preload=preload, decim=decim)

    if keep_ecg:
        # We know we have created a synthetic channel and epochs are preloaded
        ecg_raw = RawArray(
            ecg, create_info(ch_names=['ECG-SYN'],
                             sfreq=raw.info['sfreq'], ch_types=['ecg']),
            first_samp=raw.first_samp)
        ignore = ['ch_names', 'chs', 'nchan', 'bads']
        for k, v in raw.info.items():
            if k not in ignore:
                ecg_raw.info[k] = v
        syn_epochs = Epochs(ecg_raw, events=ecg_epochs.events,
                            event_id=event_id, tmin=tmin, tmax=tmax,
                            proj=False, picks=[0], baseline=baseline,
                            decim=decim, preload=True)
        ecg_epochs = ecg_epochs.add_channels([syn_epochs])

    return ecg_epochs


@verbose
def _make_ecg(inst, start, stop, reject_by_annotation=False, verbose=None):
    """Create ECG signal from cross channel average."""
    if not any(c in inst for c in ['mag', 'grad']):
        raise ValueError('Unable to generate artificial ECG channel')
    for ch in ['mag', 'grad']:
        if ch in inst:
            break
    logger.info('Reconstructing ECG signal from {}'
                .format({'mag': 'Magnetometers',
                         'grad': 'Gradiometers'}[ch]))
    picks = pick_types(inst.info, meg=ch, eeg=False, ref_meg=False)
    if isinstance(inst, BaseRaw):
        reject_by_annotation = 'omit' if reject_by_annotation else None
        ecg, times = inst.get_data(picks, start, stop, reject_by_annotation,
                                   True)
    elif isinstance(inst, BaseEpochs):
        ecg = np.hstack(inst.copy().crop(start, stop).get_data())
        times = inst.times
    elif isinstance(inst, Evoked):
        ecg = inst.data
        times = inst.times
    return ecg.mean(0, keepdims=True), times
