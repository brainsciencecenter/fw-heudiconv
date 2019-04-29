"""
(AKA dbic-bids) Flexible heuristic to establish BIDS DataLad datasets hierarchy

Initially developed and deployed at Dartmouth Brain Imaging Center
(http://dbic.dartmouth.edu) using Siemens Prisma 3T under the umbrellas of the
Center of Reproducible Neuroimaging Computation (ReproNim, http://repronim.org)
and Center for Open Neuroscience (CON, http://centerforopenneuroscience.org).

## Dataset ownership/location

Datasets will be arranged in a hierarchy similar to how study/exam cards are
arranged at the scanner console.  You should have

- "region" defined per each PI,
 - on the first level most probably as PI_StudentOrRA/ (e.g., Gobbini_Matteo)
   - StudyID_StudyName/   (e.g. 1002_faceangles)
     - Arbitrary name for the exam card -- it doesn't get into Study Description.

Selecting specific exam card would populate Study Description field using
aforementioned levels, which will be used by this heuristic to decide on the
location of the dataset.

In case of multiple sessions, it is recommended to generate separate "cards"
per each session.

## Sequence naming on the scanner console

Sequence names on the scanner must follow this specification to avoid manual
conversion/handling:

  [PREFIX:]<seqtype[-label]>[_ses-<SESID>][_task-<TASKID>][_acq-<ACQLABEL>][_run-<RUNID>][_dir-<DIR>][<more BIDS>][__<custom>]

where
 [PREFIX:] - leading capital letters followed by : are stripped/ignored
 <...> - value to be entered
 [...] - optional -- might be nearly mandatory for some modalities (e.g.,
         run for functional) and very optional for others
 *ID - alpha-numerical identifier (e.g. 01,02, pre, post, pre01) for a run,
       task, session. Note that makes more sense to use numerical values for
       RUNID (e.g., _run-01, _run-02) for obvious sorting and possibly
       descriptive ones for e.g. SESID (_ses-movie, _ses-localizer)


<seqtype[-label]>
   a known BIDS sequence type which is usually a name of the folder under
   subject's directory. And (optional) label is specific per sequence type
   (e.g. typical "bold" for func, or "T1w" for "anat"), which could often
   (but not always) be deduced from DICOM. Known to BIDS modalities are:

     anat - anatomical data.  Might also be collected multiple times across
            runs (e.g. if subject is taken out of magnet etc), so could
            (optionally) have "_run" definition attached. For "standard anat"
            labels, please consult to "8.3 Anatomy imaging data" but most
            common are 'T1w', 'T2w', 'angio'
     func - functional (AKA task, including resting state) data.
            Typically contains multiple runs, and might have multiple different
            tasks different per each run
            (e.g. _task-memory_run-01, _task-oddball_run-02)
     fmap - field maps
     dwi  - diffusion weighted imaging (also can as well have runs)

_ses-<SESID> (optional)
    a session.  Having a single sequence within a study would make that study
    follow "multi-session" layout. A common practice to have a _ses specifier
    within the scout sequence name. You can either specify explicit session
    identifier (SESID) or just say to maintain, create (starts with 1).
    You can also use _ses-{date} in case of scanning phantoms or non-human
    subjects and wanting sessions to be coded by the acquisition date.

_task-<TASKID> (optional)
    a short name for a task performed during that run.  If not provided and it
    is a func sequence, _task-UNKNOWN will be automatically added to comply with
    BIDS. Consult http://www.cognitiveatlas.org/tasks on known tasks.

_acq-<ACQLABEL> (optional)
    a short custom label to distinguish a different set of parameters used for
    acquiring the same modality (e.g. _acq-highres, _acq-lowres  etc)

_run-<RUNID> (optional)
    a (typically functional) run. The same idea as with SESID.

_dir-[AP,PA,LR,RL,VD,DV] (optional)
    to be used for fmap images, whenever a pair of the SE images is collected
    to be used to estimate the fieldmap

<more BIDS> (optional)
    any other fields (e.g. _acq-) from BIDS acquisition

__<custom> (optional)
  after two underscores any arbitrary comment which will not matter to how
  layout in BIDS. But that one theoretically should not be necessary,
  and (ab)use of it would just signal lack of thought while preparing sequence
  name to start with since everything could have been expressed in BIDS fields.

## Last moment checks/FAQ:

- Functional runs should have _task-<TASKID> field defined
- Do not use "+", "_" or "-" within SESID, TASKID, ACQLABEL, RUNID,  so we
  could detect "canceled" runs.
- If run was canceled -- just copy canceled run (with the same index) and re-run
  it. Files with overlapping name will be considered duplicate/canceled session
  and only the last one would remain.  The others would acquire
  __dup0<number>  suffix.

Although we still support "-" and "+" used within SESID and TASKID, their use is
not recommended, thus not listed here
"""

import os
import re
from collections import OrderedDict
import hashlib
from glob import glob

import logging
lgr = logging.getLogger('heudiconv')

# Terminology to hamornise and use to name variables etc
# experiment
#  subject
#   [session]
#    exam (AKA scanning session) - currently seqinfo, unless brought together from multiple
#     series  (AKA protocol?)
#      - series_spec - deduced from fields the spec (literal value)
#      - series_info - the dictionary with fields parsed from series_spec

# Which fields in seqinfo (in this order) to check for the ReproIn spec
series_spec_fields = ('protocol_name', 'series_description')



DEFAULT_FIELDS = {}


def _delete_chars(from_str, deletechars):
    """ Delete characters from string allowing for Python 2 / 3 difference
    """
    try:
        return from_str.translate(None, deletechars)
    except TypeError:
        return from_str.translate(str.maketrans('', '', deletechars))


def create_key(subdir, file_suffix, outtype=('nii.gz', 'dicom'),
               annotation_classes=None, prefix=''):
    if not subdir:
        raise ValueError('subdir must be a valid format string')
    # may be even add "performing physician" if defined??
    template = os.path.join(
        prefix,
        "{bids_subject_session_dir}",
        subdir,
        "{bids_subject_session_prefix}_%s" % file_suffix
    )
    return template, outtype, annotation_classes


def md5sum(string):
    """Computes md5sum of as string"""
    if not string:
        return ""  # not None so None was not compared to strings
    m = hashlib.md5(string.encode())
    return m.hexdigest()

def get_study_description(seqinfo):
    # Centralized so we could fix/override
    v = get_unique(seqinfo, 'study_description')
    return v

def get_study_hash(seqinfo):
    # XXX: ad hoc hack
    return md5sum(get_study_description(seqinfo))


def ls(study_session, seqinfo):
    """Additional ls output for a seqinfo"""
    #assert len(sequences) <= 1  # expecting only a single study here
    #seqinfo = sequences.keys()[0]
    return ' study hash: %s' % get_study_hash(seqinfo)


def infotodict(seqinfo):
    """Heuristic evaluator for determining which runs belong where

    allowed template fields - follow python string module:

    item: index within category
    subject: participant id
    seqitem: run number during scanning
    subindex: sub index within group
    session: scan index for longitudinal acq
    """

    lgr.info("Processing %d seqinfo entries", len(seqinfo))
    and_dicom = ('dicom', 'nii.gz')

    info = OrderedDict()
    skipped, skipped_unknown = [], []
    current_run = 0
    run_label = None   # run-
    dcm_image_iod_spec = None
    skip_derived = False
    for s in seqinfo:
        # XXX: skip derived sequences, we don't store them to avoid polluting
        # the directory, unless it is the motion corrected ones
        # (will get _rec-moco suffix)
        if skip_derived and s.is_derived and not s.is_motion_corrected:
            skipped.append(s.series_id)
            lgr.debug("Ignoring derived data %s", s.series_id)
            continue

        # possibly apply present formatting in the series_description or protocol name
        for f in 'series_description', 'protocol_name':
            s = s._replace(**{f: getattr(s, f).format(**s._asdict())})

        template = None
        suffix = ''
        seq = []

        # figure out type of image from s.image_info -- just for checking ATM
        # since we primarily rely on encoded in the protocol name information
        prev_dcm_image_iod_spec = dcm_image_iod_spec
        if len(s.image_type) > 2:
            # https://dicom.innolitics.com/ciods/cr-image/general-image/00080008
            # 0 - ORIGINAL/DERIVED
            # 1 - PRIMARY/SECONDARY
            # 3 - Image IOD specific specialization (optional)
            dcm_image_iod_spec = s.image_type[2]
            image_type_seqtype = {
                'P': 'fmap',   # phase
                'FMRI': 'func',
                'MPR': 'anat',
                # 'M': 'func',  "magnitude"  -- can be for scout, anat, bold, fmap
                'DIFFUSION': 'dwi',
                'MIP_SAG': 'anat',  # angiography
                'MIP_COR': 'anat',  # angiography
                'MIP_TRA': 'anat',  # angiography
            }.get(dcm_image_iod_spec, None)
        else:
            dcm_image_iod_spec = image_type_seqtype = None

        series_info = {}  # For please lintian and its friends
        for sfield in series_spec_fields:
            svalue = getattr(s, sfield)
            series_info = parse_series_spec(svalue)
            if series_info:  # looks like a valid spec - we are done
                series_spec = svalue
                break
            else:
                lgr.debug(
                    "Failed to parse reproin spec in .%s=%r",
                    sfield, svalue)

        if not series_info:
            series_spec = None  # we cannot know better
            lgr.warning(
                "Could not determine the series name by looking at "
                "%s fields", ', '.join(series_spec_fields))
            skipped_unknown.append(s.series_id)
            continue

        if dcm_image_iod_spec and dcm_image_iod_spec.startswith('MIP'):
            series_info['acq'] = series_info.get('acq', '') + sanitize_str(dcm_image_iod_spec)

        seqtype = series_info.pop('seqtype')
        seqtype_label = series_info.pop('seqtype_label', None)

        if image_type_seqtype and seqtype != image_type_seqtype:
            lgr.warning(
                "Deduced seqtype to be %s from DICOM, but got %s out of %s",
                image_type_seqtype, seqtype, series_spec)

        prefix = ''

        # analyze s.protocol_name (series_id is based on it) for full name mapping etc
        if seqtype == 'func' and not seqtype_label:
            if '_pace_' in series_spec:
                seqtype_label = 'pace'  # or should it be part of seq-
            else:
                # assume bold by default
                seqtype_label = 'bold'

        if seqtype == 'fmap' and not seqtype_label:
            if not dcm_image_iod_spec:
                raise ValueError("Do not know image data type yet to make decision")
            seqtype_label = {
                'M': 'magnitude',  # might want explicit {file_index}  ?
                'P': 'phasediff',
                'DIFFUSION': 'epi', # according to KODI those DWI are the EPIs we need
            }[dcm_image_iod_spec]

        # label for dwi as well
        if seqtype == 'dwi' and not seqtype_label:
            seqtype_label = 'dwi'

        run = series_info.get('run')
        if run is not None:
            # so we have an indicator for a run
            if run == '+':
                # some sequences, e.g.  fmap, would generate two (or more?)
                # sequences -- e.g. one for magnitude(s) and other ones for
                # phases.  In those we must not increment run!
                if dcm_image_iod_spec and dcm_image_iod_spec == 'P':
                    if prev_dcm_image_iod_spec != 'M':
                        raise RuntimeError(
                            "Was expecting phase image to follow magnitude "
                            "image, but previous one was %r", prev_dcm_image_iod_spec)
                else:  # and otherwise we go to the next run
                    current_run += 1
            elif run == '=':
                if not current_run:
                    current_run = 1
            elif run.isdigit():
                current_run_ = int(run)
                if current_run_ < current_run:
                    lgr.warning(
                        "Previous run (%s) was larger than explicitly specified %s",
                        current_run, current_run_)
                current_run = current_run_
            else:
                raise ValueError(
                    "Don't know how to deal with run specification %s" % repr(run))
            if isinstance(current_run, str) and current_run.isdigit():
                current_run = int(current_run)
            run_label = "run-" + ("%02d" % current_run
                                  if isinstance(current_run, int)
                                  else current_run)
        else:
            # if there is no _run -- no run label addded
            run_label = None

        # yoh: had a wrong assumption
        # if s.is_motion_corrected:
        #     assert s.is_derived, "Motion corrected images must be 'derived'"

        if s.is_motion_corrected and 'rec-' in series_info.get('bids', ''):
            raise NotImplementedError("want to add _acq-moco but there is _acq- already")

        suffix_parts = [
            None if not series_info.get('task') else "task-%s" % series_info['task'],
            None if not series_info.get('acq') else "acq-%s" % series_info['acq'],
            # But we want to add an indicator in case it was motion corrected
            # in the magnet. ref sample  /2017/01/03/qa
            None if not s.is_motion_corrected else 'rec-moco',
            series_info.get('bids'),
            run_label,
            seqtype_label,
        ]
        # filter tose which are None, and join with _
        suffix = '_'.join(filter(bool, suffix_parts))

        # # .series_description in case of
        # sdesc = s.study_description
        # # temporary aliases for those phantoms which we already collected
        # # so we rename them into this
        # #MAPPING
        #
        # # the idea ias to have sequence names in the format like
        # # bids_<subdir>_bidsrecord
        # # in bids record we could have  _run[+=]
        # #  which would say to either increment run number from already encountered
        # #  or reuse the last one
        # if seq:
        #     suffix += 'seq-%s' % ('+'.join(seq))

        # For scouts -- we want only dicoms
        # https://github.com/nipy/heudiconv/issues/145
        if "_Scout" in s.series_description or \
                (seqtype == 'anat' and seqtype_label and seqtype_label.startswith('scout')):
            outtype = ('dicom',)
        else:
            outtype = ('nii.gz', 'dicom')

        template = create_key(seqtype, suffix, prefix=prefix, outtype=outtype)
        # we wanted ordered dict for consistent demarcation of dups
        if template not in info:
            info[template] = []
        info[template].append(s.series_id)

    if skipped:
        lgr.info("Skipped %d sequences: %s" % (len(skipped), skipped))
    if skipped_unknown:
        lgr.warning("Could not figure out where to stick %d sequences: %s" %
                    (len(skipped_unknown), skipped_unknown))

    info = get_dups_marked(info)  # mark duplicate ones with __dup-0x suffix

    info = dict(info)  # convert to dict since outside functionality depends on it being a basic dict
    return info


def get_dups_marked(info, per_series=True):
    """

    Parameters
    ----------
    info
    per_series: bool
      If set to False, it would create growing index through all series. That
      could lead to non-desired effects if some "multi file" scans (such as
      fmap with magnitude{1,2} and phasediff) would not be able to associate
      multiple files for the same acquisition.   By default (True) dup indices
      would be per each series (change introduced in 0.5.2)

    Returns
    -------

    """
    # analyze for "cancelled" runs, if run number was explicitly specified and
    # thus we ended up with multiple entries which would mean that older ones
    #  were "cancelled"
    info = info.copy()
    dup_id = 0
    for template, series_ids in list(info.items()):
        if len(series_ids) > 1:
            lgr.warning("Detected %d duplicated run(s) for template %s: %s",
                        len(series_ids) - 1, template[0], series_ids[:-1])
            # copy the duplicate ones into separate ones
            if per_series:
                dup_id = 0  # reset since declared per series
            for dup_series_id in series_ids[:-1]:
                dup_id += 1
                dup_template = (
                    '%s__dup-%02d' % (template[0], dup_id),
                    ) + template[1:]
                # There must have not been such a beast before!
                if dup_template in info:
                    raise AssertionError(
                        "{} is already known to info={}. "
                        "May be a bug for per_series=True handling?"
                        "".format(dup_template, info)
                    )
                info[dup_template] = [dup_series_id]
            info[template] = series_ids[-1:]
        assert len(info[template]) == 1
    return info


def get_unique(seqinfos, attr):
    """Given a list of seqinfos, which must have come from a single study
    get specific attr, which must be unique across all of the entries

    If not -- fail!

    """
    values = set(getattr(si, attr) for si in seqinfos)
    assert (len(values) == 1)
    return values.pop()


# TODO: might need to do groupping per each session and return here multiple
# hits, or may be we could just somehow demarkate that it will be multisession
# one and so then later value parsed (again) in infotodict  would be used???
def infotoids(seqinfos, outdir):
    # decide on subjid and session based on patient_id
    lgr.info("Processing sequence infos to deduce study/session")
    study_description = get_study_description(seqinfos)
    study_description_hash = md5sum(study_description)
    subject = fixup_subjectid(get_unique(seqinfos, 'patient_id'))
    # TODO:  fix up subject id if missing some 0s
    if study_description:
        # Generally it is a ^ but if entered manually, ppl place space in it
        split = re.split('[ ^]', study_description, 1)
        # split first one even more, since couldbe PI_Student or PI-Student
        split = re.split('-|_', split[0], 1) + split[1:]

        # locator = study_description.replace('^', '/')
        locator = '/'.join(split)
    else:
        locator = 'unknown'

    # TODO: actually check if given study is study we would care about
    # and if not -- we should throw some ???? exception

    # So -- use `outdir` and locator etc to see if for a given locator/subject
    # and possible ses+ in the sequence names, so we would provide a sequence
    # So might need to go through  parse_series_spec(s.protocol_name)
    # to figure out presence of sessions.
    ses_markers = []

    # there might be fixups needed so we could deduce session etc
    # this copy is not replacing original one, so the same fix_seqinfo
    # might be called later
    seqinfos = fix_seqinfo(seqinfos)
    for s in seqinfos:
        if s.is_derived:
            continue
        session_ = parse_series_spec(s.protocol_name).get('session', None)
        if session_ and '{' in session_:
            # there was a marker for something we could provide from our seqinfo
            # e.g. {date}
            session_ = session_.format(**s._asdict())
        ses_markers.append(session_)
    ses_markers = list(filter(bool, ses_markers))  # only present ones
    session = None
    if ses_markers:
        # we have a session or possibly more than one even
        # let's figure out which case we have
        nonsign_vals = set(ses_markers).difference('+=')
        # although we might want an explicit '=' to note the same session as
        # mentioned before?
        if len(nonsign_vals) > 1:
            lgr.warning( #raise NotImplementedError(
                "Cannot deal with multiple sessions in the same study yet!"
                " We will process until the end of the first session"
            )
        if nonsign_vals:
            # get only unique values
            ses_markers = list(set(ses_markers))
            if set(ses_markers).intersection('+='):
                raise NotImplementedError(
                    "Should not mix hardcoded session markers with incremental ones (+=)"
                )
            if not len(ses_markers) == 1:
                raise NotImplementedError(
                    "Should have got a single session marker.  Got following: %s"
                    % ', '.join(map(repr, ses_markers))
                )
            session = ses_markers[0]
        else:
            # TODO - I think we are doomed to go through the sequence and split
            # ... actually the same as with nonsign_vals, we just would need to figure
            # out initial one if sign ones, and should make use of knowing
            # outdir
            #raise NotImplementedError()
            # we need to look at what sessions we already have
            sessions_dir = os.path.join(outdir, locator, 'sub-' + subject)
            prior_sessions = sorted(glob(os.path.join(sessions_dir, 'ses-*')))
            # TODO: more complicated logic
            # For now just increment session if + and keep the same number if =
            # and otherwise just give it 001
            # Note: this disables our safety blanket which would refuse to process
            # what was already processed before since it would try to override,
            # BUT there is no other way besides only if heudiconv was storing
            # its info based on some UID
            if ses_markers == ['+']:
                session = '%03d' % (len(prior_sessions) + 1)
            elif ses_markers == ['=']:
                session = os.path.basename(prior_sessions[-1])[4:] if prior_sessions else '001'
            else:
                session = '001'


    if study_description_hash == '9d148e2a05f782273f6343507733309d':
        session = 'siemens1'
        lgr.info('Imposing session {0}'.format(session))

    return {
        # TODO: request info on study from the JedCap
        'locator': locator,
        # Sessions to be deduced yet from the names etc TODO
        'session': session,
        'subject': subject,
    }


def sanitize_str(value):
    """Remove illegal characters for BIDS from task/acq/etc.."""
    return _delete_chars(value, '#!@$%^&.,:;_-')


def parse_series_spec(series_spec):
    """Parse protocol name according to our convention with minimal set of fixups
    """
    # Since Yarik didn't know better place to put it in, but could migrate outside
    # at some point. TODO
    series_spec = series_spec.replace("anat_T1w", "anat-T1w")
    series_spec = series_spec.replace("hardi_64", "dwi_acq-hardi64")
    series_spec = series_spec.replace("AAHead_Scout", "anat-scout")

    # Parse the name according to our convention/specification

    # leading or trailing spaces do not matter
    series_spec = series_spec.strip(' ')

    # Strip off leading CAPITALS: prefix to accommodate some reported usecases:
    # https://github.com/ReproNim/reproin/issues/14
    # where PU: prefix is added by the scanner
    series_spec = re.sub("^[A-Z]*:", "", series_spec)

    # Remove possible suffix we don't care about after __
    series_spec = series_spec.split('__', 1)[0]

    bids = None  # we don't know yet for sure
    # We need to figure out if it is a valid bids
    split = series_spec.split('_')
    prefix = split[0]

    # Fixups
    if prefix == 'scout':
        prefix = split[0] = 'anat-scout'

    if prefix != 'bids' and '-' in prefix:
        prefix, _ = prefix.split('-', 1)
    if prefix == 'bids':
        bids = True  # for sure
        split = split[1:]

    def split2(s):
        # split on - if present, if not -- 2nd one returned None
        if '-' in s:
            return s.split('-', 1)
        return s, None

    # Let's analyze first element which should tell us sequence type
    seqtype, seqtype_label = split2(split[0])
    if seqtype not in {'anat', 'func', 'dwi', 'behav', 'fmap'}:
        # It is not something we don't consume
        if bids:
            lgr.warning("It was instructed to be BIDS sequence but unknown "
                        "type %s found", seqtype)
        return {}

    regd = dict(seqtype=seqtype)
    if seqtype_label:
        regd['seqtype_label'] = seqtype_label
    # now go through each to see if one which we care
    bids_leftovers = []
    for s in split[1:]:
        key, value = split2(s)
        if value is None and key[-1] in "+=":
            value = key[-1]
            key = key[:-1]

        # sanitize values, which must not have _ and - is undesirable ATM as well
        # TODO: BIDSv2.0 -- allows "-" so replace with it instead
        value = str(value).replace('_', 'X').replace('-', 'X')

        if key in ['ses', 'run', 'task', 'acq']:
            # those we care about explicitly
            regd[{'ses': 'session'}.get(key, key)] = sanitize_str(value)
        else:
            bids_leftovers.append(s)

    if bids_leftovers:
        regd['bids'] = '_'.join(bids_leftovers)

    # TODO: might want to check for all known "standard" BIDS suffixes here
    # among bids_leftovers, thus serve some kind of BIDS validator

    # if not regd.get('seqtype_label', None):
    #     # might need to assign a default label for each seqtype if was not
    #     # given
    #     regd['seqtype_label'] = {
    #         'func': 'bold'
    #     }.get(regd['seqtype'], None)

    return regd


def fixup_subjectid(subjectid):
    """Just in case someone managed to miss a zero or added an extra one"""
    # make it lowercase
    subjectid = subjectid.lower()
    reg = re.match("sid0*(\d+)$", subjectid)
    if not reg:
        # some completely other pattern
        # just filter out possible _- in it
        return re.sub('[-_]', '', subjectid)
    return "sid%06d" % int(reg.groups()[0])
