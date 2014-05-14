# -*- coding: utf-8 -*-
#_____________________________________________________________________________
#
# This file is part of BridgeDB, a Tor bridge distribution system.
#
# :authors: Isis Lovecruft 0xA3ADB67A2CDB8B35 <isis@torproject.org>
#           Aaron Gibson   0x2C4B239DD876C9F6 <aagbsn@torproject.org>
#           Nick Mathewson 0x21194EBB165733EA <nickm@torproject.org>
#           please also see AUTHORS file
# :copyright: (c) 2007-2014, The Tor Project, Inc.
#             (c) 2007-2014, all entities within the AUTHORS file
#             (c) 2014, Isis Lovecruft
# :license: see LICENSE for licensing information
#_____________________________________________________________________________

"""This module implements various methods for obtaining or creating CAPTCHAs.

**Module Overview:**

::

  captcha
   |- CaptchaExpired - Raised if a solution is given for a stale CAPTCHA.
   |- CaptchaKeyError - Raised if a CAPTCHA system's keys are invalid/missing.
   |- GimpCaptchaError - Raised when a Gimp CAPTCHA can't be retrieved.
   |
   \_ ICaptcha - Zope Interface specification for a generic CAPTCHA.
        |
      Captcha - Generic base class implementation for obtaining a CAPTCHA.
      |  |- image - The CAPTCHA image.
      |  |- challenge - A unique string associated with this CAPTCHA image.
      |  |- publicKey - The public key for this CAPTCHA system.
      |  |- secretKey - The secret key for this CAPTCHA system.
      |   \_ get() - Get a new pair of CAPTCHA image and challenge strings.
      |
      |- ReCaptcha - Obtain reCaptcha images and challenge strings.
      |   \_ get() - Request an image and challenge from a reCaptcha API server.
      |
      \_ GimpCaptcha - Class for obtaining a CAPTCHA from a local cache.
          |- hmacKey - A client-specific key for HMAC generation.
          |- cacheDir - The path to the local CAPTCHA cache directory.
          \_ get() - Get a CAPTCHA image from the cache and create a challenge.

..

There are two types of CAPTCHAs which BridgeDB knows how to serve: those
obtained by from a reCaptcha_ API server with
:class:`~bridgedb.captcha.Raptcha`, and those which have been generated with
gimp-captcha_ and then cached locally.

.. _reCaptcha : https://code.google.com/p/recaptcha/
.. _gimp-captcha: https://github.com/isislovecruft/gimp-captcha
"""

from base64 import urlsafe_b64encode
from base64 import urlsafe_b64decode

import logging
import random
import os
import urllib2

from BeautifulSoup import BeautifulSoup

from zope.interface import Interface, Attribute, implements

from bridgedb import crypto
from bridgedb.txrecaptcha import API_SSL_SERVER


class CaptchaKeyError(Exception):
    """Raised if a CAPTCHA system's keys are invalid or missing."""

class GimpCaptchaError(Exception):
    """General exception raised when a Gimp CAPTCHA cannot be retrieved."""


class ICaptcha(Interface):
    """Interface specification for CAPTCHAs."""

    image = Attribute(
        "A string containing the contents of a CAPTCHA image file.")
    challenge = Attribute(
        "A unique string associated with the dispursal of this CAPTCHA.")
    publicKey = Attribute(
        "A public key used for encrypting CAPTCHA challenge strings.")
    secretKey = Attribute(
        "A private key used for decrypting challenge strings during CAPTCHA"
        "solution verification.")

    def get():
        """Retrieve a new CAPTCHA image."""


class Captcha(object):
    """A generic CAPTCHA base class.

    :ivar str image: The CAPTCHA image.
    :ivar str challenge: A challenge string which should permit checking of
        the client's CAPTCHA solution in some manner. In stateless protocols
        such as HTTP, this should be passed along to the client with the
        CAPTCHA image.
    :ivar publicKey: A public key used for encrypting CAPTCHA challenge strings.
    :ivar secretKey: A private key used for decrypting challenge strings during
        CAPTCHA solution verification.
    """
    implements(ICaptcha)

    def __init__(self, publicKey=None, secretKey=None):
        """Obtain a new CAPTCHA for a client."""
        self.image = None
        self.challenge = None
        self.publicKey = publicKey
        self.secretKey = secretKey

    def get(self):
        return self.image


class ReCaptcha(Captcha):
    """A reCaptcha CAPTCHA.

    :ivar str image: The CAPTCHA image.
    :ivar str challenge: The ``'recaptcha_challenge_response'`` HTTP form
        field to pass to the client along with the CAPTCHA image.
    """

    def __init__(self, pubkey=None, privkey=None):
        """Create a new ReCaptcha CAPTCHA.

        :param str pubkey: The public reCaptcha API key.
        :param str privkey: The private reCaptcha API key.
        """
        super(ReCaptcha, self).__init__()
        self.pubkey = pubkey
        self.privkey = privkey

    def get(self):
        """Retrieve a CAPTCHA from the reCaptcha API server.

        This simply requests a new CAPTCHA from
        ``recaptcha.client.captcha.API_SSL_SERVER`` and parses the returned
        HTML to extract the CAPTCHA image and challenge string. The image is
        stored at ``ReCaptcha.image`` and the challenge string at
        ``ReCaptcha.challenge``.

        :raises CaptchaKeyError: If either the :ivar:`publicKey` or
            :ivar:`secretKey` are missing.
        :raises HTTPError: If the server returned any HTTP error status code.
        """
        if not self.pubkey or not self.privkey:
            raise CaptchaKeyError('You must supply recaptcha API keys')

        urlbase = API_SSL_SERVER
        form = "/noscript?k=%s" % self.pubkey

        # Extract and store image from recaptcha
        html = urllib2.urlopen(urlbase + form).read()
        # FIXME: The remaining lines currently cannot be reliably unit tested:
        soup = BeautifulSoup(html)                           # pragma: no cover
        imgurl = urlbase + "/" +  soup.find('img')['src']    # pragma: no cover
        cField = soup.find(                                  # pragma: no cover
            'input', {'name': 'recaptcha_challenge_field'})  # pragma: no cover
        self.challenge = str(cField['value'])                # pragma: no cover
        self.image = urllib2.urlopen(imgurl).read()          # pragma: no cover


class GimpCaptcha(Captcha):
    """A cached CAPTCHA image which was created with Gimp."""

    def __init__(self, secretKey=None, publicKey=None, hmacKey=None,
                 cacheDir=None):
        """Create a ``GimpCaptcha`` which retrieves images from **cacheDir**.

        :param str secretkey: A PKCS#1 OAEP-padded, private RSA key, used for
            verifying the client's solution to the CAPTCHA.
        :param str publickey: A PKCS#1 OAEP-padded, public RSA key, used for
            creating the ``captcha_challenge_field`` string to give to a
            client.
        :param bytes hmacKey: A client-specific HMAC secret key.
        :param str cacheDir: The local directory which pre-generated CAPTCHA
            images have been stored in. This can be set via the
            ``GIMP_CAPTCHA_DIR`` setting in the config file.
        :raises GimpCaptchaError: if **cacheDir** is not a directory.
        :raises CaptchaKeyError: if any of **secretKey**, **publicKey**,
            or **hmacKey** is invalid, or missing.
        """
        super(GimpCaptcha, self).__init__()

        if not cacheDir or not os.path.isdir(cacheDir):
            raise GimpCaptchaError("Gimp captcha cache isn't a directory: %r"
                                   % cacheDir)
        if not (publicKey and secretKey and hmacKey):
            raise CaptchaKeyError(
                "Invalid key supplied to GimpCaptcha: SK=%r PK=%r HMAC=%r"
                % (secretKey, publicKey, hmacKey))

        self.secretKey = secretKey
        self.publicKey = publicKey
        self.cacheDir = cacheDir
        self.hmacKey = hmacKey
        self.answer = None

    @classmethod
    def check(cls, challenge, solution, secretKey, hmacKey):
        """Check a client's CAPTCHA **solution** against the **challenge**.

        :param str challenge: The contents of the
            ``'captcha_challenge_field'`` HTTP form field.
        :param str solution: The client's proposed solution to the CAPTCHA
            that they were presented with.
        :param str secretkey: A PKCS#1 OAEP-padded, private RSA key, used for
            verifying the client's solution to the CAPTCHA.
        :param bytes hmacKey: A private key for generating HMACs.
        :rtype: bool
        :returns: True if the CAPTCHA solution was correct.
        """
        validHMAC = False

        if not solution:
            return validHMAC

        logging.debug("Checking CAPTCHA solution %r against challenge %r"
                      % (solution, challenge))
        try:
            decoded = urlsafe_b64decode(challenge)
            hmac = decoded[:20]
            original = decoded[20:]
            verified = crypto.getHMAC(hmacKey, original)
            validHMAC = verified == hmac
        except Exception:
            return False
        finally:
            if validHMAC:
                try:
                    decrypted = secretKey.decrypt(original)
                except Exception as error:
                    logging.warn(error.message)
                else:
                    if solution.lower() == decrypted.lower():
                        return True
            return False

    def createChallenge(self, answer):
        """Encrypt the CAPTCHA **answer** and HMAC the encrypted data.

        Take a string containing the answer to a CAPTCHA and encrypts it to
        :attr:`publicKey`. The resulting encrypted blob is then HMACed with a
        client-specific :attr:`hmacKey`. These two strings are then joined
        together in the form:

                HMAC ";" ENCRYPTED_ANSWER

        where the HMAC MUST be the first 20 bytes. Lastly base64-encoded (in a
        URL safe manner).

        :param str answer: The answer to a CAPTCHA.
        :rtype: str
        :returns: An HMAC of, as well as a string containing the URL-safe,
            base64-encoded encrypted **answer**.
        """
        encrypted = self.publicKey.encrypt(answer)
        hmac = crypto.getHMAC(self.hmacKey, encrypted)
        challenge = hmac + encrypted
        encoded = urlsafe_b64encode(challenge)
        return encoded

    def get(self):
        """Get a random CAPTCHA from the cache directory.

        This chooses a random CAPTCHA image file from the cache directory, and
        reads the contents of the image into a string. Next, it creates a
        challenge string for the CAPTCHA, via :meth:`createChallenge`.

        :raises GimpCaptchaError: if the chosen CAPTCHA image file could not
            be read, or if the **cacheDir** is empty.
        :rtype: tuple
        :returns: A 2-tuple containing the image file contents as a string,
            and a challenge string (used for checking the client's solution).
        """
        try:
            imageFilename = random.choice(os.listdir(self.cacheDir))
            imagePath = os.path.join(self.cacheDir, imageFilename)
            with open(imagePath) as imageFile:
                self.image = imageFile.read()
        except IndexError:
            raise GimpCaptchaError("CAPTCHA cache dir appears empty: %r"
                                   % self.cacheDir)
        except (OSError, IOError):
            raise GimpCaptchaError("Could not read Gimp captcha image file: %r"
                                   % imageFilename)

        self.answer = imageFilename.rsplit(os.path.extsep, 1)[0]
        self.challenge = self.createChallenge(self.answer)

        return (self.image, self.challenge)
