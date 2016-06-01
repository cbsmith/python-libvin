"""
Copyright (C) 2016 Dan Kegel
Look up EPA mileage given make, model, and year.
License: LGPL
"""

# See "Practical Data Science Cookbook" for a nice look at this data in Python.
# Also, several students did writeups of how to look at this data in R, e.g.
# https://rpubs.com/jeknov/auto-eda
# https://rpubs.com/agz1117/ConnectedCar

import csv
import os
import os.path
from collections import namedtuple
from pkg_resources import resource_stream

from logging import getLogger

logger = getLogger(__name__)

epa_loaded = False
epa_table = {}

def epa_mmy_lookup(make, model, year):
    '''Return a tuple of EPA fuel economy data, including especially
       UCity -- city MPG
       UHighway -- highway MPG
       co2TailpipeGpm -- grams per mile co2 emissions
       See http://www.fueleconomy.gov/feg/ws/index.shtml for definitions of other fields
       Not threadsafe
    '''
    global epa_loaded
    global epa_table
    if not epa_loaded:
        with resource_stream('libvin', 'epa/vehicles.csv') as f:
            csv_f = csv.reader(f)
            headers = next(csv_f)
            Row = namedtuple('Row', headers)
            for line in csv_f:
                row = Row(*line)
                mmy = row.make + "_" + row.model + "_" + str(row.year)
                print "%s: city %s, hwy %s, co2/mi %s" % (mmy, row.UCity, row.UHighway, row.co2TailpipeGpm)
                epa_table[mmy] = row
        epa_loaded = True

    return epa_table[make + "_" + model + "_" + str(year)]

EPA_URL = 'http://www.fueleconomy.gov/feg/epadata/vehicles.csv.zip'
IF_NONE_MATCH = 'If-None-Match'
IF_MODIFIED_SINCE = 'If-Modified-Since'
LAST_MODIFIED = 'Last-Modified'
ETAG = 'ETag'

def open_resource_stream(x):
    path = 'epa/vehicles.{}'.format(x)
    if os.path.exists(os.path.join('libvin', 'path')):
        logger.info('Opening %s resource %s', 'libvin', path)
    else:
        logger.warn('Opening non-existant %s resource %s', 'libvin', path)
    return resource_stream('libvin', path)

def make_stream_opener(target_dir):
    def open_file_stream(x):
        path = os.path.join(target_dir, 'vehicles.{}'.format(x))
        if os.path.exists(path):
            logger.info('Opening file resource %s', path)
        else:
            logger.warn('Opening non-existant file resource %s', path)
        return open(path)
    return open_file_stream

def ensure_latest(target_dir=None):
    etag = None
    last_modified = None

    if target_dir is None:
        target_dir = os.path.join('libvin', 'epa')
        stream_opener = open_resource_stream
    else:
        stream_opener = make_stream_opener(target_dir)

    try:
        logger.info('Opening etag')
        with stream_opener('etag') as f:
            etag = f.read(2048) # that's a big buffer for an etag
            logger.info('ETag: %s', etag)
    except IOError as e:
        logger.warn('Problem reading etag %s', str(e))

    try:
        logger.info('Opening last_modified')
        with stream_opener('last_modified') as f:
            last_modified = f.read(2048)
            logger.info('Last-Modified: %s', last_modified)
    except IOError as e:
        logger.warn('Problem reading last_modified %s', str(e))

    response = get_epa_updates(etag=etag, last_modified=last_modified)
    if response.code != 304:
        logger.info('Response code says we have new data')
        new_etag = response.info()['ETag']
        new_last_modified = response.info()[LAST_MODIFIED]
        from contextlib import closing
        from shutil import rmtree
        from tempfile import mkstemp
        import zipfile

        # probably should make sure we use the same directory name
        fd, file_path = mkstemp()
        try:
            with closing(os.fdopen(fd, 'w')) as temp_file:
                somebytes = response.read()
                while len(somebytes) > 0:
                    temp_file.write(somebytes)
                    somebytes = response.read()

            extracted_dir = os.path.join(target_dir, 'tmp')
            try:
                with zipfile.ZipFile(file_path) as zip_file:
                    namelist = zip_file.namelist()
                    if len(namelist) != 1:
                        namelist = filter('vehicles.csv'.__eq__, namelist)
                    name = namelist[0]
                    zip_file.extract(name, extracted_dir)

                extracted_path = os.path.join(extracted_dir, name)
                target_path = os.path.join(target_dir, 'vehicles.csv')
                os.rename(extracted_path, target_path)
                etag_path = os.path.join(target_dir, 'vehicles.etag')
                with open(etag_path, 'w') as f:
                    f.write(new_etag)
                    logger.info('Successfully wrote out etag %s to %s', new_etag, etag_path)
                last_modified_path = os.path.join(target_dir, 'vehicles.last_modified')
                with open(last_modified_path, 'w') as f:
                    f.write(new_last_modified)
                    logger.info('Successfullly wrote out last_modified %s to %s', new_last_modified, last_modified_path)
            finally:
                try:
                    rmtree(extracted_dir)
                    logger.info('Erased %s', extracted_dir)
                except OSError:
                    #probably just never really got created, but should log this at some point
                    logger.exception('Error erasing %s', extracted_dir)
        finally:
            #belts and suspenders here
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.warn('Cleaned up stray file at %s', file_path)
        return True
    else:
        logger.info('get_epa_updates(etag=%s, last_modified=%s) got some new data', etag, last_modified)
        return False

def get_epa_updates(etag=None, last_modified=None, url=EPA_URL):
    '''
    Get back updates from the EPA, if they have been modified.
    etag - etag from last update
    last_modified - last modified date for the URL, can be in datetime (TZ-aware or UTC), time.struct_time or just raw string
    url - possible override of the EPA_URL to download it from

    Returns a urllib2 'instance' object (file like, but with a getcode() header)
    '''

    #poor form to lazy import this, but on the other hand, this shouldn't normally be invoked
    import datetime
    import time
    try:
        import urllib.request as urlcompat
    except ImportError:
        # evil compatibility hack, I'd love to use Requests, but... more dependencies
        import urllib2 as urlcompat

    class NotFoundHandler(urlcompat.HTTPDefaultErrorHandler):
        def http_error_304(self, req, fp, code, msg, hdrs):
            assert code == 304
            result = urlcompat.HTTPError(req.get_full_url(), code, msg, hdrs, fp)
            result.status = code
            return result

    request = urlcompat.Request(url)
    if etag:
        request.add_header(IF_NONE_MATCH, etag)
    # time is always a pain, and actually worse than url resolution in python.
    # this is a best effort to get to RFC 1123-compliant timestamp from a variety
    # of possible formats totally annoying
    if last_modified is not None and last_modified != '':
        # should use six to work with strings, but trying to minimize dependencies
        if isinstance(last_modified, datetime.datetime):
            last_modified = last_modified.utctimetuple() # now it is struct_time, skipping next step
        elif isinstance(last_modified, (float,int,long)):
            last_modified = time.gmtime(last_modified)   # now it is time.time, so it'll hit the next line

        if isinstance(last_modified, time.struct_time):
            last_modified = time.strftime('%a, %d %b %Y %H:%M:%S GMT', last_modified) # now  a string
        if isinstance(last_modified, basestring):
            request.add_header(IF_MODIFIED_SINCE, last_modified)

    # Try to let them know where the trouble is coming from
    request.add_header('User-Agent', 'Mozilla/5.0 compatible urllib on behalf of python-libvin: https://github.com/h3/python-libvin')

    logger.info('Building opener for request %s with headers %s', request, request.headers)
    # Now we have something resembling a proper request
    opener = urlcompat.build_opener(NotFoundHandler)
    return opener.open(request)
