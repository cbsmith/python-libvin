# -*- coding: utf-8 -*-
from __future__ import with_statement
from contextlib import closing
from nose.tools import assert_equals, assert_not_equals, assert_true, assert_false, raises, assert_almost_equals
from nose.plugins.attrib import attr
import time
from warnings import warn

from libvin import epa

from logging import getLogger

logger = getLogger(__name__)

@attr('net')
class TestEPA(object):
    def test_default_download(self):
        with closing(epa.get_epa_updates()) as response:
            assert_equals(response.code, 200, "Failed to get a 200 from {}".format(epa.EPA_URL))
            length = response.headers['Content-Length']
            try:
                length = int(length)
            except ValueError:
                self.fail('Content length not a valid number {}'.format(length))

            assert_true(True)

    @attr(speed='slow')
    def test_default_download_payload(self):
        response = epa.get_epa_updates()

        content_length = response.headers['Content-Length']
        length = int(content_length)

        downloaded = 0

        some_bytes = response.read(4096)
        while some_bytes:
            downloaded += len(some_bytes)
            some_bytes = response.read(4096)

        assert_equals(downloaded, length, 'Downloaded {} bytes but Content-Length is {}'.format(downloaded, content_length))

    @attr(speed='slow')
    def test_ensure_latest(self):
        logger.error('test_ensure_latest(%s)', self)
        from tempfile import mkdtemp
        from shutil import rmtree
        tempdir = mkdtemp()
        updated = False
        try:
            import os.path
            if os.path.exists(tempdir):
                logger.warn('Temporary path %s in place', tempdir)
            else:
                logger.error('For some reason the temporary path %s cannot be found', tempdir)
            result = epa.ensure_latest(tempdir)
            logger.debug('First ensure for %s gets a %s', tempdir, result)
            logger.warn('Second pass in place')
            if os.path.exists(tempdir):
                logger.info('Temporary path %s in place', tempdir)
            else:
                logger.warn('Temporary path %s is not place', tempdir)
            updated = epa.ensure_latest(tempdir)
            assert_false(updated, "Somehow a second ensure operation still thought updates were needed")
        finally:
            if not updated:
                rmtree(tempdir)


@attr('net')
class TestIfModified(object):
    STALE_DATE='Thu, 01 Jan 1970 00:00:00 GMT'
    
    @classmethod
    def setup_class(klass):
        '''
        Simple fixture to cache etag & last_modified values so we're not constantly downloading them.
        '''

        if getattr(klass, 'etag', None) is None:
            response = epa.get_epa_updates()
            assert_equals(200, response.code, 'Unexpected response code when setting up if-modified tests')
            klass.etag = response.headers['ETag']
            klass.last_modified = response.headers['Last-Modified']

    @staticmethod
    def get_updates_expect_304(etag=None, last_modified=None, *args, **kwargs):
        response = epa.get_epa_updates(etag, last_modified, *args, **kwargs)
        if response.code != 304:
            # small miraculous chance that the object *just* changed, so let's retry
            warn('ETag appears to have changed since the tests started, checking again')
            # strip out any possible args
            if etag:
                etag = response.info()['ETag']
            if last_modified:
                last_modified = response.info()['Last-Modified']
            response = epa.get_epa_updates(etag, last_modified, *args, **kwargs)
        return response
    
    def test_download_etag(self):
        response = self.get_updates_expect_304(etag=self.etag)
        assert_equals(response.code, 304, 'ETag appears to be different from expected')
        assert_equals('', response.read(), 'Unexpectedly found content in a 304 response')

    def test_bad_etag(self):
        response = epa.get_epa_updates(etag='wrong etag')
        assert_not_equals(response.code, 304, 'Bad etag still gets us a 304')
        assert_not_equals('', response.read(), 'Unexpectedly did not get content with a 304 response')

    def test_download_exact_last_modified(self):
        response = self.get_updates_expect_304(last_modified=self.last_modified)
        assert_equals(response.code, 304, 'Last modified appears to be different from expected')
        assert_equals('', response.read(), 'Unexpectedly found content in a 304 response')

    def test_download_etag_and_last_modified(self):
        response = self.get_updates_expect_304(etag=self.etag, last_modified=self.last_modified)
        assert_equals(response.code, 304, 'Did not get 304 when specifying etag & last_modified time.')
        assert_equals('', response.read(), 'Unexpectedly found content in a 304 response')

    def test_download_wrong_etag_and_last_modified(self):
        response = epa.get_epa_updates(etag='wrong_etag', last_modified=self.last_modified)
        assert_not_equals(response.code, 304, 'Oddly not detecting a modified ETag when we pass in the correct last modified date.')
        assert_not_equals('', response.read(), 'Unexpectedly did not get content when presenting wrong etag')

    def test_download_etag_and_stale_last_modified(self):
        response = epa.get_epa_updates(etag=self.etag, last_modified=self.STALE_DATE)
        assert_not_equals(response.code, 304, 'Oddly not detecting a modified last modified date when we pass in the correct etag.')
        assert_not_equals('', response.read(), 'Unexpectedly did not get content when presenting wrong last modified date')

    @attr(speed='slow')
    def test_old_and_new_by_type(self):
        '''
        Test old and new If-Modified-Since flags with a variety of types.
        '''

        EPOCH_STRING='Thu, 01 Jan 1970 00:00:00 GMT'

        from datetime import datetime
        from calendar import timegm
        from dateutil.tz import tzutc, tzlocal
        
        # and since Python sucks, we have a multitude of different types to represent timestamps
        from functools import partial
        time_transform_chain = (str,                                        # start with a string
                                rfc1123_strptime,                           # to time.struct_time
                                timegm,                                     # to int epoch seconds
                                long,                                       # to long epoch seconds
                                float,                                      # to float epoch seconds
                                datetime.utcfromtimestamp,                  # to datime without timezone
                                partial(datetime.replace, tzinfo=tzutc()),  # to datetime with UTC timezone
                                partial(datetime.astimezone, tz=tzlocal())) # to datetime with local timezone
        from itertools import imap
        sometimes = (EPOCH_STRING, get_fueleconomy_time())
        for f in time_transform_chain:
            sometimes = tuple(imap(f, sometimes))
            yield (self.check_last_modified,) + sometimes

        # yield EPOCH_STRING, sometime
        # sometime = time.strptime(sometime, '%a, %d %b %Y %H:%M:%S %Z')
        # yield time.gmtime(0), sometime
        # sometime = calendar.timegm(sometime)
        # yield 0, sometime
        # yield long(0), long(sometime)
        # yield float(0), float(sometime)
        # dts = (datetime.datetime(1970,1,1), datetime.datetime.utcfromtimestamp(sometime))
        # yield dts
        # dts = tuple(d.replace(tzinfo=dateutil.tz.tzutc()) for d in dts)
        # yield dts
        # yield tuple(d.astimezone(dateutil.tz.tzlocal()) for d in dts)

    def check_last_modified(self, epoch_time, now, etag=None):
        response = epa.get_epa_updates(last_modified=now)
        assert_equals(response.code, 304, 'Somehow the document has been modified since *now* when sending as type {}'.format(type(now)))
        assert_equals('', response.read(), 'Unexpected did not get content with a 304 response when sending as type {}'.format(type(now)))
        response = epa.get_epa_updates(last_modified=epoch_time)
        assert_not_equals(response.code, 304, 'Somehow did not get a 304 when sending a {} for the epoch'.format(type(epoch_time)))
        assert_not_equals('', response.read(), 'Somehow got content when sending a {} for the epoch'.format(type(epoch_time)))

def get_fueleconomy_time():
    # IIS sucks, and is apparently unconcerned about clockskew.
    # Consequently, timestamps from the near future never get a 304.
    # So, we have to query the server with a HEAD request and look for
    # the Date field to figure out what time it is in fueleconomy.com

    try:
        import urllib.request as test_urllib
    except:
        import urllib2 as test_urllib

    head_req = test_urllib.Request(epa.EPA_URL)
    opener = test_urllib.build_opener()
    # and since urllib sucks, we have to override get_method to do a HEAD request
    head_req.get_method = lambda : 'HEAD'
    resp = opener.open(head_req)
    return resp.info()['Date']

def rfc1123_strptime(a_string):
    return time.strptime(a_string, '%a, %d %b %Y %H:%M:%S %Z')

