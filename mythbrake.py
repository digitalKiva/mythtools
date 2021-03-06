#!/usr/bin/env python2.7
# -*- coding: UTF-8 -*-

# 2015 Michael Stucky
# This script is based on Raymond Wagner's transcode wrapper stub.
# Designed to be a USERJOB of the form </path to script/transcode-h264.py %JOBID%>

from MythTV import Job, Recorded, System, MythDB, findfile, MythError, MythLog, datetime

from optparse import OptionParser
from glob import glob
from shutil import copyfile
import sys
import os
import errno
import time
import re

log_dir = '/home/mythtv/mythbrake_logs'
transcoder = '/usr/bin/HandBrakeCLI'
flush_commskip = True
build_seektable = True

class tee:
    'redirects a write to multiple objects'
    def __init__(self, *writers):
        self.writers = writers
    def write(self, string):
        string = string.strip()
        if len(string) > 0:
            string = '%s\t%s\n' % (time.strftime('%m-%d %H:%M:%S'), string)
            for writer in self.writers:
                writer.write(string)
                writer.flush()

def runjob(jobid=None, chanid=None, starttime=None):
    db = MythDB()
    if jobid:
        job = Job(jobid, db=db)
        chanid = job.chanid
        starttime = job.starttime
    rec = Recorded((chanid, starttime), db=db)

    timestr = time.strftime("%m-%d-%y %H:%M:%S")
    title_san = re.sub("\s", ".", rec.title)
    print title_san
    try:
        os.mkdir(log_dir)
    except OSError, e:
        pass
    log_file = os.path.join(log_dir, "%s_transcode_log_%s.txt" % (title_san, timestr))
    trans_log_file = os.path.join(log_dir, "%s_transcode_log_%s.hb.txt" % (title_san, timestr))
    commflag_log_file = os.path.join(log_dir, "%s_transcode_log_%s.cf.txt" % (title_san, timestr))
    print 'Capturing log in %s...' % log_file
    lfp = open(log_file, 'w')
    sys.stdout = tee(sys.stdout, lfp)
    sys.stderr = tee(sys.stderr, lfp)

    print 'Logging into %s' % log_file

    sg = findfile('/'+rec.basename, rec.storagegroup, db=db)
    if sg is None:
        print 'Local access to recording not found.'
        sys.exit(1)

    infile = os.path.join(sg.dirname, rec.basename)
    tmpfile = '%s.tmp' % infile.rsplit('.',1)[0]
    outfile = '%s.mp4' % infile.rsplit('.',1)[0]

    print "Infile: %s" % infile
    print "Outfile: %s" % outfile

    # reformat 'starttime' for use with mythtranscode/ffmpeg/mythcommflag
    starttime = str(rec.starttime.utcisoformat().replace(u':', '').replace(u' ', '').replace(u'T', '').replace('-', ''))

    # Lossless transcode to strip cutlist
    if rec.cutlist == 1:
        if jobid:
            job.update({'status':4, 'comment':'Removing Cutlist'})

        task = System(path='mythtranscode', db=db)
        try:
            output = task('--chanid "%s"' % chanid,
                          '--starttime "%s"' % starttime,
                          '--mpeg2',
                          '--honorcutlist',
                          '-o "%s"' % tmpfile,
                          '2> /dev/null')
        except MythError, e:
            print 'Command failed with output:\n%s' % e.stderr
            if jobid:
                job.update({'status':304, 'comment':'Removing Cutlist failed'})
            sys.exit(e.retcode)
    else:
        tmpfile = infile
        # copyfile('%s' % infile, '%s' % tmpfile)

    # Transcode to mp4
    if jobid:
        job.update({'status':4, 'comment':'Transcoding to mp4'})

    task = System(path=transcoder, db=db)
    try:
        output = task('-v',
                      '-q 20.0',
                      '-e x264',
                      '-r 25',
                      '--crop 0:0:0:0',
                      '-d',
                      '-m',
                      '-x b-adapt=2:rc-lookahead=50:ref=3:bframes=3:me=umh:subme=8:trellis=1:merange=20:direct=auto',
                      '-i "%s"' % tmpfile,
                      '-o "%s"' % outfile,
                      '-4',
                      '--optimize 2 >> "%s"' % trans_log_file)
    except MythError, e:
        print 'Command failed with output:\n%s' % e.stderr
        if jobid:
            job.update({'status':304, 'comment':'Transcoding to mp4 failed'})
        sys.exit(e.retcode)

    print 'Done transcoding'

    rec.basename = os.path.basename(outfile)
    try:
        print 'Deleting %s' % infile
        os.remove(infile)
    except OSError:
        pass
    print '''Cleanup the old *.png files'''
    for filename in glob('%s*.png' % infile):
        print 'Deleting %s' % filename
        os.remove(filename)
    try:
        print 'Deleting %s' % tmpfile
        os.remove(tmpfile)
    except OSError:
        pass
    try:
        print 'Deleting %s.map' % tmpfile
        os.remove('%s.map' % tmpfile)
    except OSError:
        pass
    rec.filesize = os.path.getsize(outfile)
    rec.transcoded = 1
    rec.seek.clean()

    print 'Changed recording basename, set transcoded...'

    if flush_commskip:
        print 'Flushing commskip list...'
        for index,mark in reversed(list(enumerate(rec.markup))):
            if mark.type in (rec.markup.MARK_COMM_START, rec.markup.MARK_COMM_END):
                del rec.markup[index]
        rec.bookmark = 0
        rec.cutlist = 0
        rec.markup.commit()

    print 'Updating recording...'
    rec.update()

    if jobid:
        job.update({'status':4, 'comment':'Rebuilding seektable'})

    if build_seektable:
        print 'Rebuilding seek table'
        task = System(path='mythcommflag')
        task.command('--chanid %s' % chanid,
                     '--starttime %s' % starttime,
                     '--rebuild',
                     '> "%s"' % commflag_log_file)

    print 'Job Done!...'
    if jobid:
        job.update({'status':272, 'comment':'Transcode Completed'})

def main():
    parser = OptionParser(usage="usage: %prog [options] [jobid]")

    parser.add_option('--chanid', action='store', type='int', dest='chanid',
            help='Use chanid for manual operation')
    parser.add_option('--starttime', action='store', type='int', dest='starttime',
            help='Use starttime for manual operation')
    parser.add_option('-v', '--verbose', action='store', type='string', dest='verbose',
            help='Verbosity level')

    opts, args = parser.parse_args()

    if opts.verbose:
        if opts.verbose == 'help':
            print MythLog.helptext
            sys.exit(0)
        MythLog._setlevel(opts.verbose)

    if len(args) == 1:
        runjob(jobid=args[0])
    elif opts.chanid and opts.starttime:
        runjob(chanid=opts.chanid, starttime=opts.starttime)
    else:
        print 'Script must be provided jobid, or chanid and starttime.'
        sys.exit(1)

if __name__ == '__main__':
    main()

