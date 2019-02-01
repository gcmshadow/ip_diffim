# This file is part of ip_diffim.
#
# LSST Data Management System
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
# See COPYRIGHT file at the top of the source tree.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <https://www.lsstcorp.org/LegalNotices/>.
#

import numpy as np
from scipy import ndimage
from lsst.afw.coord.refraction import differentialRefraction
import lsst.afw.geom as afwGeom
from lsst.afw.geom import AffineTransform
from lsst.afw.geom import makeTransform
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath
from lsst.geom import radians

__all__ = ["DcrModel", "applyDcr", "calculateDcr", "calculateImageParallacticAngle"]


class DcrModel:
    """A model of the true sky after correcting chromatic effects.

    Attributes
    ----------
    dcrNumSubfilters : `int`
        Number of sub-filters used to model chromatic effects within a band.
    modelImages : `list` of `lsst.afw.image.MaskedImage`
        A list of masked images, each containing the model for one subfilter

    Notes
    -----
    The ``DcrModel`` contains an estimate of the true sky, at a higher
    wavelength resolution than the input observations. It can be forward-
    modeled to produce Differential Chromatic Refraction (DCR) matched
    templates for a given ``Exposure``, and provides utilities for conditioning
    the model in ``dcrAssembleCoadd`` to avoid oscillating solutions between
    iterations of forward modeling or between the subfilters of the model.
    """

    def __init__(self, modelImages, filterInfo=None, psf=None):
        self.dcrNumSubfilters = len(modelImages)
        self.modelImages = modelImages
        self._filter = filterInfo
        self._psf = psf

    @classmethod
    def fromImage(cls, maskedImage, dcrNumSubfilters, filterInfo=None, psf=None):
        """Initialize a DcrModel by dividing a coadd between the subfilters.

        Parameters
        ----------
        maskedImage : `lsst.afw.image.MaskedImage`
            Input coadded image to divide equally between the subfilters.
        dcrNumSubfilters : `int`
            Number of sub-filters used to model chromatic effects within a band.
        filterInfo : `lsst.afw.image.Filter`, optional
            The filter definition, set in the current instruments' obs package.
            Required for any calculation of DCR, including making matched templates.
        psf : `lsst.afw.detection.Psf`, optional
            Point spread function (PSF) of the model.
            Required if the ``DcrModel`` will be persisted.

        Returns
        -------
        dcrModel : `lsst.pipe.tasks.DcrModel`
            Best fit model of the true sky after correcting chromatic effects.
        """
        # NANs will potentially contaminate the entire image,
        # depending on the shift or convolution type used.
        model = maskedImage.clone()
        badPixels = np.isnan(model.image.array) | np.isnan(model.variance.array)
        model.image.array[badPixels] = 0.
        model.variance.array[badPixels] = 0.
        model.image.array /= dcrNumSubfilters
        # We divide the variance by N and not N**2 because we will assume each
        # subfilter is independent. That means that the significance of
        # detected sources will be lower by a factor of sqrt(N) in the
        # subfilter images, but we will recover it when we combine the
        # subfilter images to construct matched templates.
        model.variance.array /= dcrNumSubfilters
        model.mask.array[badPixels] = model.mask.getPlaneBitMask("NO_DATA")
        modelImages = [model, ]
        for subfilter in range(1, dcrNumSubfilters):
            modelImages.append(model.clone())
        return cls(modelImages, filterInfo, psf)

    @classmethod
    def fromDataRef(cls, dataRef, datasetType="dcrCoadd", numSubfilters=None, **kwargs):
        """Load an existing DcrModel from a repository.

        Parameters
        ----------
        dataRef : `lsst.daf.persistence.ButlerDataRef`
            Data reference defining the patch for coaddition and the
            reference Warp
        datasetType : `str`, optional
            Name of the DcrModel in the registry {"dcrCoadd", "dcrCoadd_sub"}
        numSubfilters : `int`
            Number of sub-filters used to model chromatic effects within a band.
        **kwargs
            Additional keyword arguments to pass to look up the model in the data registry.
            Common keywords and their types include: ``tract``:`str`, ``patch``:`str`,
            ``bbox``:`lsst.afw.geom.Box2I`

        Returns
        -------
        dcrModel : `lsst.pipe.tasks.DcrModel`
            Best fit model of the true sky after correcting chromatic effects.
        """
        modelImages = []
        filterInfo = None
        psf = None
        for subfilter in range(numSubfilters):
            dcrCoadd = dataRef.get(datasetType, subfilter=subfilter,
                                   numSubfilters=numSubfilters, **kwargs)
            if filterInfo is None:
                filterInfo = dcrCoadd.getFilter()
            if psf is None:
                psf = dcrCoadd.getPsf()
            modelImages.append(dcrCoadd.maskedImage)
        return cls(modelImages, filterInfo, psf)

    def __len__(self):
        """Return the number of subfilters.

        Returns
        -------
        dcrNumSubfilters : `int`
            The number of DCR subfilters in the model.
        """
        return self.dcrNumSubfilters

    def __getitem__(self, subfilter):
        """Iterate over the subfilters of the DCR model.

        Parameters
        ----------
        subfilter : `int`
            Index of the current ``subfilter`` within the full band.
            Negative indices are allowed, and count in reverse order
            from the highest ``subfilter``.

        Returns
        -------
        modelImage : `lsst.afw.image.MaskedImage`
            The DCR model for the given ``subfilter``.

        Raises
        ------
        IndexError
            If the requested ``subfilter`` is greater or equal to the number
            of subfilters in the model.
        """
        if np.abs(subfilter) >= len(self):
            raise IndexError("subfilter out of bounds.")
        return self.modelImages[subfilter]

    def __setitem__(self, subfilter, maskedImage):
        """Update the model image for one subfilter.

        Parameters
        ----------
        subfilter : `int`
            Index of the current subfilter within the full band.
        maskedImage : `lsst.afw.image.MaskedImage`
            The DCR model to set for the given ``subfilter``.

        Raises
        ------
        IndexError
            If the requested ``subfilter`` is greater or equal to the number
            of subfilters in the model.
        ValueError
            If the bounding box of the new image does not match.
        """
        if np.abs(subfilter) >= len(self):
            raise IndexError("subfilter out of bounds.")
        if maskedImage.getBBox() != self.bbox:
            raise ValueError("The bounding box of a subfilter must not change.")
        self.modelImages[subfilter] = maskedImage

    @property
    def filter(self):
        """Return the filter of the model.

        Returns
        -------
        filter : `lsst.afw.image.Filter`
            The filter definition, set in the current instruments' obs package.
        """
        return self._filter

    @property
    def psf(self):
        """Return the psf of the model.

        Returns
        -------
        psf : `lsst.afw.detection.Psf`
            Point spread function (PSF) of the model.
        """
        return self._psf

    @property
    def bbox(self):
        """Return the common bounding box of each subfilter image.

        Returns
        -------
        bbox : `lsst.afw.geom.Box2I`
            Bounding box of the DCR model.
        """
        return self[0].getBBox()

    @property
    def mask(self):
        """Return the common mask of each subfilter image.

        Returns
        -------
        bbox : `lsst.afw.image.Mask`
            Mask plane of the DCR model.
        """
        return self[0].mask

    def getReferenceImage(self, bbox=None):
        """Calculate a reference image from the average of the subfilter images.

        Parameters
        ----------
        bbox : `lsst.afw.geom.Box2I`, optional
            Sub-region of the coadd. Returns the entire image if `None`.

        Returns
        -------
        refImage : `numpy.ndarray`
            The reference image with no chromatic effects applied.
        """
        bbox = bbox or self.bbox
        return np.mean([model[bbox].image.array for model in self], axis=0)

    def assign(self, dcrSubModel, bbox=None):
        """Update a sub-region of the ``DcrModel`` with new values.

        Parameters
        ----------
        dcrSubModel : `lsst.pipe.tasks.DcrModel`
            New model of the true scene after correcting chromatic effects.
        bbox : `lsst.afw.geom.Box2I`, optional
            Sub-region of the coadd.
            Defaults to the bounding box of ``dcrSubModel``.

        Raises
        ------
        ValueError
            If the new model has a different number of subfilters.
        """
        if len(dcrSubModel) != len(self):
            raise ValueError("The number of DCR subfilters must be the same "
                             "between the old and new models.")
        bbox = bbox or self.bbox
        for model, subModel in zip(self, dcrSubModel):
            model.assign(subModel[bbox], bbox)

    def buildMatchedTemplate(self, exposure=None, warpCtrl=None,
                             visitInfo=None, bbox=None, wcs=None, mask=None,
                             splitSubfilters=False):
        """Create a DCR-matched template image for an exposure.

        Parameters
        ----------
        exposure : `lsst.afw.image.Exposure`, optional
            The input exposure to build a matched template for.
            May be omitted if all of the metadata is supplied separately
        warpCtrl : `lsst.afw.Math.WarpingControl`, optional
            Configuration settings for warping an image.
            If not set, defaults to a lanczos3 warping kernel for the image,
            and a bilinear kernel for the mask
        visitInfo : `lsst.afw.image.VisitInfo`, optional
            Metadata for the exposure. Ignored if ``exposure`` is set.
        bbox : `lsst.afw.geom.Box2I`, optional
            Sub-region of the coadd. Ignored if ``exposure`` is set.
        wcs : `lsst.afw.geom.SkyWcs`, optional
            Coordinate system definition (wcs) for the exposure.
            Ignored if ``exposure`` is set.
        mask : `lsst.afw.image.Mask`, optional
            reference mask to use for the template image.
        splitSubfilters : `bool`, optional
            Calculate DCR for two evenly-spaced wavelengths in each subfilter,
            instead of at the midpoint. Default: False

        Returns
        -------
        templateImage : `lsst.afw.image.maskedImageF`
            The DCR-matched template

        Raises
        ------
        ValueError
            If neither ``exposure`` or all of ``visitInfo``, ``bbox``, and ``wcs`` are set.
        """
        if self.filter is None:
            raise ValueError("'filterInfo' must be set for the DcrModel in order to calculate DCR.")
        if exposure is not None:
            visitInfo = exposure.getInfo().getVisitInfo()
            bbox = exposure.getBBox()
            wcs = exposure.getInfo().getWcs()
        elif visitInfo is None or bbox is None or wcs is None:
            raise ValueError("Either exposure or visitInfo, bbox, and wcs must be set.")
        if warpCtrl is None:
            # Turn off the warping cache, since we set the linear interpolation length to the entire subregion
            # This warper is only used for applying DCR shifts, which are assumed to be uniform across a patch
            warpCtrl = afwMath.WarpingControl("lanczos3", "bilinear",
                                              cacheSize=0, interpLength=max(bbox.getDimensions()))

        dcrShift = calculateDcr(visitInfo, wcs, self.filter, len(self), splitSubfilters=splitSubfilters)
        templateImage = afwImage.MaskedImageF(bbox)
        for subfilter, dcr in enumerate(dcrShift):
            templateImage += applyDcr(self[subfilter][bbox], dcr, warpCtrl, splitSubfilters=splitSubfilters)
        if mask is not None:
            templateImage.setMask(mask[bbox])
        return templateImage

    def buildMatchedExposure(self, exposure=None, warpCtrl=None,
                             visitInfo=None, bbox=None, wcs=None, mask=None):
        """Wrapper to create an exposure from a template image.

        Parameters
        ----------
        exposure : `lsst.afw.image.Exposure`, optional
            The input exposure to build a matched template for.
            May be omitted if all of the metadata is supplied separately
        warpCtrl : `lsst.afw.Math.WarpingControl`
            Configuration settings for warping an image
        visitInfo : `lsst.afw.image.VisitInfo`, optional
            Metadata for the exposure. Ignored if ``exposure`` is set.
        bbox : `lsst.afw.geom.Box2I`, optional
            Sub-region of the coadd. Ignored if ``exposure`` is set.
        wcs : `lsst.afw.geom.SkyWcs`, optional
            Coordinate system definition (wcs) for the exposure.
            Ignored if ``exposure`` is set.
        mask : `lsst.afw.image.Mask`, optional
            reference mask to use for the template image.

        Returns
        -------
        templateExposure : `lsst.afw.image.exposureF`
            The DCR-matched template
        """
        templateImage = self.buildMatchedTemplate(exposure, warpCtrl, visitInfo, bbox, wcs, mask)
        templateExposure = afwImage.ExposureF(bbox, wcs)
        templateExposure.setMaskedImage(templateImage)
        templateExposure.setPsf(self.psf)
        templateExposure.setFilter(self.filter)
        return templateExposure

    def conditionDcrModel(self, modelImages, bbox, gain=1.):
        """Average two iterations' solutions to reduce oscillations.

        Parameters
        ----------
        modelImages : `list` of `lsst.afw.image.MaskedImage`
            The new DCR model images from the current iteration.
            The values will be modified in place.
        bbox : `lsst.afw.geom.Box2I`
            Sub-region of the coadd
        gain : `float`, optional
            Relative weight to give the new solution when updating the model.
            Defaults to 1.0, which gives equal weight to both solutions.
        """
        # Calculate weighted averages of the image and variance planes.
        # Note that ``newModel *= gain`` would multiply the variance by ``gain**2``
        for model, newModel in zip(self, modelImages):
            newModel.image *= gain
            newModel.image += model[bbox].image
            newModel.image /= 1. + gain
            newModel.variance *= gain
            newModel.variance += model[bbox].variance
            newModel.variance /= 1. + gain

    def regularizeModelIter(self, subfilter, newModel, bbox, regularizationFactor,
                            regularizationWidth=2):
        """Restrict large variations in the model between iterations.

        Parameters
        ----------
        subfilter : `int`
            Index of the current subfilter within the full band.
        newModel : `lsst.afw.image.MaskedImage`
            The new DCR model for one subfilter from the current iteration.
            Values in ``newModel`` that are extreme compared with the last
            iteration are modified in place.
        bbox : `lsst.afw.geom.Box2I`
            Sub-region to coadd
        regularizationFactor : `float`
            Maximum relative change of the model allowed between iterations.
        regularizationWidth : int, optional
            Minimum radius of a region to include in regularization, in pixels.
        """
        refImage = self[subfilter][bbox].image.array
        highThreshold = np.abs(refImage)*regularizationFactor
        lowThreshold = refImage/regularizationFactor
        newImage = newModel.image.array
        self.applyImageThresholds(newImage, highThreshold=highThreshold, lowThreshold=lowThreshold,
                                  regularizationWidth=regularizationWidth)

    def regularizeModelFreq(self, modelImages, bbox, statsCtrl, regularizationFactor,
                            regularizationWidth=2, mask=None, convergenceMaskPlanes="DETECTED"):
        """Restrict large variations in the model between subfilters.

        Parameters
        ----------
        modelImages : `list` of `lsst.afw.image.MaskedImage`
            The new DCR model images from the current iteration.
            The values will be modified in place.
        bbox : `lsst.afw.geom.Box2I`
            Sub-region to coadd
        statsCtrl : `lsst.afw.math.StatisticsControl`
            Statistics control object for coaddition.
        regularizationFactor : `float`
            Maximum relative change of the model allowed between subfilters.
        regularizationWidth : `int`, optional
            Minimum radius of a region to include in regularization, in pixels.
        mask : `lsst.afw.image.Mask`, optional
            Optional alternate mask
        convergenceMaskPlanes : `list` of `str`, or `str`, optional
            Mask planes to use to calculate convergence.

        Notes
        -----
        This implementation of frequency regularization restricts each subfilter
        image to be a smoothly-varying function times a reference image.
        """
        # ``regularizationFactor`` is the maximum change between subfilter images, so the maximum difference
        # between one subfilter image and the average will be the square root of that.
        maxDiff = np.sqrt(regularizationFactor)
        noiseLevel = self.calculateNoiseCutoff(modelImages[0], statsCtrl, bufferSize=5, mask=mask, bbox=bbox)
        referenceImage = self.getReferenceImage(bbox)
        badPixels = np.isnan(referenceImage) | (referenceImage <= 0.)
        if np.sum(~badPixels) == 0:
            # Skip regularization if there are no valid pixels
            return
        referenceImage[badPixels] = 0.
        filterWidth = regularizationWidth
        fwhm = 2.*filterWidth
        # The noise should be lower in the smoothed image by sqrt(Nsmooth) ~ fwhm pixels
        noiseLevel /= fwhm
        smoothRef = ndimage.filters.gaussian_filter(referenceImage, filterWidth) + noiseLevel

        baseThresh = np.ones_like(referenceImage)
        highThreshold = baseThresh*maxDiff
        lowThreshold = baseThresh/maxDiff
        for subfilter, model in enumerate(modelImages):
            smoothModel = ndimage.filters.gaussian_filter(model.image.array, filterWidth) + noiseLevel
            relativeModel = smoothModel/smoothRef
            # Now sharpen the smoothed relativeModel using an alpha of 3.
            relativeModel2 = ndimage.filters.gaussian_filter(relativeModel, filterWidth/3.)
            relativeModel = relativeModel + 3.*(relativeModel - relativeModel2)
            self.applyImageThresholds(relativeModel,
                                      highThreshold=highThreshold,
                                      lowThreshold=lowThreshold,
                                      regularizationWidth=regularizationWidth)
            relativeModel *= referenceImage
            modelImages[subfilter].image.array = relativeModel

    def calculateNoiseCutoff(self, maskedImage, statsCtrl, bufferSize,
                             convergenceMaskPlanes="DETECTED", mask=None, bbox=None):
        """Helper function to calculate the background noise level of an image.

        Parameters
        ----------
        maskedImage : `lsst.afw.image.MaskedImage`
            The input image to evaluate the background noise properties.
        statsCtrl : `lsst.afw.math.StatisticsControl`
            Statistics control object for coaddition.
        bufferSize : `int`
            Number of additional pixels to exclude
            from the edges of the bounding box.
        convergenceMaskPlanes : `list` of `str`, or `str`
            Mask planes to use to calculate convergence.
        mask : `lsst.afw.image.Mask`, Optional
            Optional alternate mask
        bbox : `lsst.afw.geom.Box2I`, optional
            Sub-region of the masked image to calculate the noise level over.

        Returns
        -------
        noiseCutoff : `float`
            The threshold value to treat pixels as noise in an image..
        """
        if bbox is None:
            bbox = self.bbox
        if mask is None:
            mask = maskedImage[bbox].mask
        bboxShrink = afwGeom.Box2I(bbox)
        bboxShrink.grow(-bufferSize)
        convergeMask = mask.getPlaneBitMask(convergenceMaskPlanes)

        backgroundPixels = mask[bboxShrink].array & (statsCtrl.getAndMask() | convergeMask) == 0
        noiseCutoff = np.std(maskedImage[bboxShrink].image.array[backgroundPixels])
        return noiseCutoff

    def applyImageThresholds(self, image, highThreshold=None, lowThreshold=None, regularizationWidth=2):
        """Restrict image values to be between upper and lower limits.

        This method flags all pixels in an image that are outside of the given
        threshold values. The threshold values are taken from a reference image,
        so noisy pixels are likely to get flagged. In order to exclude those
        noisy pixels, the array of flags is eroded and dilated, which removes
        isolated pixels outside of the thresholds from the list of pixels to be
        modified. Pixels that remain flagged after this operation have their
        values set to the appropriate upper or lower threshold value.

        Parameters
        ----------
        image : `numpy.ndarray`
            The image to apply the thresholds to.
            The values will be modified in place.
        highThreshold : `numpy.ndarray`, optional
            Array of upper limit values for each pixel of ``image``.
        lowThreshold : `numpy.ndarray`, optional
            Array of lower limit values for each pixel of ``image``.
        regularizationWidth : `int`, optional
            Minimum radius of a region to include in regularization, in pixels.
        """
        # Generate the structure for binary erosion and dilation, which is used to remove noise-like pixels.
        # Groups of pixels with a radius smaller than ``regularizationWidth``
        # will be excluded from regularization.
        filterStructure = ndimage.iterate_structure(ndimage.generate_binary_structure(2, 1),
                                                    regularizationWidth)
        if highThreshold is not None:
            highPixels = image > highThreshold
            if regularizationWidth > 0:
                # Erode and dilate ``highPixels`` to exclude noisy pixels.
                highPixels = ndimage.morphology.binary_opening(highPixels, structure=filterStructure)
            image[highPixels] = highThreshold[highPixels]
        if lowThreshold is not None:
            lowPixels = image < lowThreshold
            if regularizationWidth > 0:
                # Erode and dilate ``lowPixels`` to exclude noisy pixels.
                lowPixels = ndimage.morphology.binary_opening(lowPixels, structure=filterStructure)
            image[lowPixels] = lowThreshold[lowPixels]


def applyDcr(maskedImage, dcr, warpCtrl, bbox=None, useInverse=False, splitSubfilters=False):
    """Shift a masked image.

    Parameters
    ----------
    maskedImage : `lsst.afw.image.MaskedImage`
        The input masked image to shift.
    dcr : `lsst.afw.geom.Extent2I`
        Shift calculated with ``calculateDcr``.
    warpCtrl : `lsst.afw.math.WarpingControl`
        Configuration settings for warping an image
    bbox : `lsst.afw.geom.Box2I`, optional
        Sub-region of the masked image to shift.
        Shifts the entire image if None (Default).
    useInverse : `bool`, optional
        Use the reverse of ``dcr`` for the shift. Default: False
    splitSubfilters : `bool`, optional
        Calculate DCR for two evenly-spaced wavelengths in each subfilter,
        instead of at the midpoint. Default: False

    Returns
    -------
    shiftedImage : `lsst.afw.image.maskedImageF`
        A masked image, with the pixels within the bounding box shifted.
    """
    padValue = afwImage.pixel.SinglePixelF(0., maskedImage.mask.getPlaneBitMask("NO_DATA"), 0)
    if bbox is None:
        bbox = maskedImage.getBBox()
    if splitSubfilters:
        shiftedImage = afwImage.MaskedImageF(bbox)
        transform0 = makeTransform(AffineTransform((-1.0 if useInverse else 1.0)*dcr[0]))
        afwMath.warpImage(shiftedImage, maskedImage[bbox],
                          transform0, warpCtrl, padValue=padValue)
        shiftedImage1 = afwImage.MaskedImageF(bbox)
        transform1 = makeTransform(AffineTransform((-1.0 if useInverse else 1.0)*dcr[1]))
        afwMath.warpImage(shiftedImage1, maskedImage[bbox],
                          transform1, warpCtrl, padValue=padValue)
        shiftedImage += shiftedImage1
        shiftedImage /= 2.
    else:
        shiftedImage = afwImage.MaskedImageF(bbox)
        transform = makeTransform(AffineTransform((-1.0 if useInverse else 1.0)*dcr))
        afwMath.warpImage(shiftedImage, maskedImage[bbox],
                          transform, warpCtrl, padValue=padValue)
    return shiftedImage


def calculateDcr(visitInfo, wcs, filterInfo, dcrNumSubfilters, splitSubfilters=False):
    """Calculate the shift in pixels of an exposure due to DCR.

    Parameters
    ----------
    visitInfo : `lsst.afw.image.VisitInfo`
        Metadata for the exposure.
    wcs : `lsst.afw.geom.SkyWcs`
        Coordinate system definition (wcs) for the exposure.
    filterInfo : `lsst.afw.image.Filter`
        The filter definition, set in the current instruments' obs package.
    dcrNumSubfilters : `int`
        Number of sub-filters used to model chromatic effects within a band.
    splitSubfilters : `bool`, optional
        Calculate DCR for two evenly-spaced wavelengths in each subfilter,
        instead of at the midpoint. Default: False

    Returns
    -------
    dcrShift : `lsst.afw.geom.Extent2I`
        The 2D shift due to DCR, in pixels.
    """
    rotation = calculateImageParallacticAngle(visitInfo, wcs)
    dcrShift = []
    weight = [0.75, 0.25]
    lambdaEff = filterInfo.getFilterProperty().getLambdaEff()
    for wl0, wl1 in wavelengthGenerator(filterInfo, dcrNumSubfilters):
        # Note that diffRefractAmp can be negative, since it's relative to the midpoint of the full band
        diffRefractAmp0 = differentialRefraction(wavelength=wl0, wavelengthRef=lambdaEff,
                                                 elevation=visitInfo.getBoresightAzAlt().getLatitude(),
                                                 observatory=visitInfo.getObservatory(),
                                                 weather=visitInfo.getWeather())
        diffRefractAmp1 = differentialRefraction(wavelength=wl1, wavelengthRef=lambdaEff,
                                                 elevation=visitInfo.getBoresightAzAlt().getLatitude(),
                                                 observatory=visitInfo.getObservatory(),
                                                 weather=visitInfo.getWeather())
        if splitSubfilters:
            diffRefractPix0 = diffRefractAmp0.asArcseconds()/wcs.getPixelScale().asArcseconds()
            diffRefractPix1 = diffRefractAmp1.asArcseconds()/wcs.getPixelScale().asArcseconds()
            diffRefractArr = [diffRefractPix0*weight[0] + diffRefractPix1*weight[1],
                              diffRefractPix0*weight[1] + diffRefractPix1*weight[0]]
            shiftX = [diffRefractPix*np.sin(rotation.asRadians()) for diffRefractPix in diffRefractArr]
            shiftY = [diffRefractPix*np.cos(rotation.asRadians()) for diffRefractPix in diffRefractArr]
            dcrShift.append((afwGeom.Extent2D(shiftX[0], shiftY[0]), afwGeom.Extent2D(shiftX[1], shiftY[1])))
        else:
            diffRefractAmp = (diffRefractAmp0 + diffRefractAmp1)/2.
            diffRefractPix = diffRefractAmp.asArcseconds()/wcs.getPixelScale().asArcseconds()
            shiftX = diffRefractPix*np.sin(rotation.asRadians())
            shiftY = diffRefractPix*np.cos(rotation.asRadians())
            dcrShift.append(afwGeom.Extent2D(shiftX, shiftY))
    return dcrShift


def calculateImageParallacticAngle(visitInfo, wcs):
    """Calculate the total sky rotation angle of an exposure.

    Parameters
    ----------
    visitInfo : `lsst.afw.image.VisitInfo`
        Metadata for the exposure.
    wcs : `lsst.afw.geom.SkyWcs`
        Coordinate system definition (wcs) for the exposure.

    Returns
    -------
    `lsst.geom.Angle`
        The rotation of the image axis, East from North.
        Equal to the parallactic angle plus any additional rotation of the
        coordinate system.
        A rotation angle of 0 degrees is defined with
        North along the +y axis and East along the +x axis.
        A rotation angle of 90 degrees is defined with
        North along the +x axis and East along the -y axis.
    """
    parAngle = visitInfo.getBoresightParAngle().asRadians()
    cd = wcs.getCdMatrix()
    if wcs.isFlipped:
        cdAngle = (np.arctan2(-cd[0, 1], cd[0, 0]) + np.arctan2(cd[1, 0], cd[1, 1]))/2.
    else:
        cdAngle = (np.arctan2(cd[0, 1], -cd[0, 0]) + np.arctan2(cd[1, 0], cd[1, 1]))/2.
    rotAngle = (cdAngle + parAngle)*radians
    return rotAngle


def wavelengthGenerator(filterInfo, dcrNumSubfilters):
    """Iterate over the wavelength endpoints of subfilters.

    Parameters
    ----------
    filterInfo : `lsst.afw.image.Filter`
        The filter definition, set in the current instruments' obs package.
    dcrNumSubfilters : `int`
        Number of sub-filters used to model chromatic effects within a band.

    Yields
    ------
    `tuple` of two `float`
        The next set of wavelength endpoints for a subfilter, in nm.
    """
    lambdaMin = filterInfo.getFilterProperty().getLambdaMin()
    lambdaMax = filterInfo.getFilterProperty().getLambdaMax()
    wlStep = (lambdaMax - lambdaMin)/dcrNumSubfilters
    for wl in np.linspace(lambdaMin, lambdaMax, dcrNumSubfilters, endpoint=False):
        yield (wl, wl + wlStep)