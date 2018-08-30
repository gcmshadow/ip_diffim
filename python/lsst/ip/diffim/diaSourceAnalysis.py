#!/usr/bin/env python

#
# LSST Data Management System
# Copyright 2008-2016 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#

__all__ = ["DiaSourceAnalystConfig", "DiaSourceAnalyst"]

import lsst.afw.image as afwImage
from lsst.log import Log
import numpy as num
import lsst.pex.config as pexConfig

scaling = 5


class DiaSourceAnalystConfig(pexConfig.Config):
    srcBadMaskPlanes = pexConfig.ListField(
        dtype=str,
        doc="""Mask planes that lead to an invalid detection.
                 Options: NO_DATA EDGE SAT BAD CR INTRP
                 E.g. : NO_DATA SAT BAD allows CR-masked and interpolated pixels""",
        default=("NO_DATA", "EDGE", "SAT", "BAD")
    )
    fBadPixels = pexConfig.Field(
        dtype=float,
        doc="Fraction of bad pixels allowed in footprint",
        default=0.1
    )
    fluxPolarityRatio = pexConfig.Field(
        dtype=float,
        doc="Minimum fraction of flux in correct-polarity pixels",
        default=0.75
    )
    nPolarityRatio = pexConfig.Field(
        dtype=float,
        doc="Minimum fraction of correct-polarity pixels in unmasked subset",
        default=0.7
    )
    nMaskedRatio = pexConfig.Field(
        dtype=float,
        doc="Minimum fraction of correct-polarity unmasked to masked pixels",
        default=0.6,
    )
    nGoodRatio = pexConfig.Field(
        dtype=float,
        doc="Minimum fraction of correct-polarity unmasked to all pixels",
        default=0.5
    )


class DiaSourceAnalyst(object):

    def __init__(self, config):
        self.config = config
        self.log = Log.getLogger("ip.diffim.DiaSourceAnalysis")

        self.bitMask = 0
        srcBadMaskPlanes = self.config.srcBadMaskPlanes
        for maskPlane in srcBadMaskPlanes:
            self.bitMask |= afwImage.Mask.getPlaneBitMask(maskPlane)

    def countDetected(self, mask):
        idxP = num.where(mask & afwImage.Mask.getPlaneBitMask("DETECTED"))
        idxN = num.where(mask & afwImage.Mask.getPlaneBitMask("DETECTED_NEGATIVE"))
        return len(idxP[0]), len(idxN[0])

    def countMasked(self, mask):
        idxM = num.where(mask & self.bitMask)
        return len(idxM[0])

    def countPolarity(self, mask, pixels):
        unmasked = ((mask & self.bitMask) == 0)
        idxP = num.where((pixels >= 0) & unmasked)
        idxN = num.where((pixels < 0) & unmasked)
        fluxP = num.sum(pixels[idxP])
        fluxN = num.sum(pixels[idxN])
        return len(idxP[0]), len(idxN[0]), fluxP, fluxN

    def testSource(self, source, subMi):
        imArr, maArr, varArr = subMi.getArrays()
        flux = source.getApFlux()

        nPixels = subMi.getWidth() * subMi.getHeight()
        nPos, nNeg, fPos, fNeg = self.countPolarity(maArr, imArr)
        nDetPos, nDetNeg = self.countDetected(maArr)
        nMasked = self.countMasked(maArr)
        assert(nPixels == (nMasked + nPos + nNeg))

        # 1) Too many pixels in the detection are masked
        fMasked = (nMasked / nPixels)
        fMaskedTol = self.config.fBadPixels
        if fMasked > fMaskedTol:
            self.log.debug("Candidate %d : BAD fBadPixels %.2f > %.2f", source.getId(), fMasked, fMaskedTol)
            return False

        if flux > 0:
            # positive-going source
            fluxRatio = fPos / (fPos + abs(fNeg))
            ngoodRatio = nPos / nPixels
            maskRatio = nPos / (nPos + nMasked)
            npolRatio = nPos / (nPos + nNeg)
        else:
            # negative-going source
            fluxRatio = abs(fNeg) / (fPos + abs(fNeg))
            ngoodRatio = nNeg / nPixels
            maskRatio = nNeg / (nNeg + nMasked)
            npolRatio = nNeg / (nNeg + nPos)

        # 2) Not enough flux in unmasked correct-polarity pixels
        fluxRatioTolerance = self.config.fluxPolarityRatio
        if fluxRatio < fluxRatioTolerance:
            self.log.debug("Candidate %d : BAD flux polarity %.2f < %.2f (pos=%.2f neg=%.2f)",
                           source.getId(), fluxRatio, fluxRatioTolerance, fPos, fNeg)
            return False

        # 3) Not enough unmasked pixels of correct polarity
        polarityTolerance = self.config.nPolarityRatio
        if npolRatio < polarityTolerance:
            self.log.debug("Candidate %d : BAD polarity count %.2f < %.2f (pos=%d neg=%d)",
                           source.getId(), npolRatio, polarityTolerance, nPos, nNeg)
            return False

        # 4) Too many masked vs. correct polarity pixels
        maskedTolerance = self.config.nMaskedRatio
        if maskRatio < maskedTolerance:
            self.log.debug("Candidate %d : BAD unmasked count %.2f < %.2f (pos=%d neg=%d mask=%d)",
                           source.getId(), maskRatio, maskedTolerance, nPos, nNeg, nMasked)
            return False

        # 5) Too few unmasked, correct polarity pixels
        ngoodTolerance = self.config.nGoodRatio
        if ngoodRatio < ngoodTolerance:
            self.log.debug("Candidate %d : BAD good pixel count %.2f < %.2f (pos=%d neg=%d tot=%d)",
                           source.getId(), ngoodRatio, ngoodTolerance, nPos, nNeg, nPixels)
            return False

        self.log.debug("Candidate %d : OK flux=%.2f nPos=%d nNeg=%d nTot=%d nDetPos=%d nDetNeg=%d "
                       "fPos=%.2f fNeg=%2f",
                       source.getId(), flux, nPos, nNeg, nPixels, nDetPos, nDetNeg, fPos, fNeg)
        return True
