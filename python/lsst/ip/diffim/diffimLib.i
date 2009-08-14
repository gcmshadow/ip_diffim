// -*- lsst-c++ -*-
%define diffimLib_DOCSTRING
"
Python bindings for lsst::ip::diffim code
"
%enddef

%feature("autodoc", "1");
%module(docstring=diffimLib_DOCSTRING, package="lsst.ip.diffim") diffimLib

// Avoid Swig bug exposed in Ticket 640
// http://dev.lsstcorp.org/trac/ticket/640
%ignore lsst::ip::diffim::FindSetBits::operator();
%ignore lsst::ip::diffim::FindCounts::operator();
%ignore lsst::ip::diffim::ImageStatistics::operator();

// Reference for this file is at http://dev.lsstcorp.org/trac/wiki/SwigFAQ 
// Nice practical example is at http://dev.lsstcorp.org/trac/browser/DMS/afw/trunk/python/lsst/afw/image/imageLib.i 

// Suppress swig complaints
#pragma SWIG nowarn=314                 // print is a python keyword (--> _print)
#pragma SWIG nowarn=362                 // operator=  ignored

// Remove warnings
// Nothing known about 'boost::bad_any_cast'
namespace boost {
    class bad_any_cast; 
    //typedef unsigned short boost::uint16_t;
}

/* handle C++ arguments that should be outputs in python */
%apply int& OUTPUT { int& };
%apply float& OUTPUT { float& };
%apply double& OUTPUT { double& };

%{
#include <boost/shared_ptr.hpp>

#include <lsst/afw/image.h>
#include <lsst/afw/math.h>
#include <lsst/afw/detection.h>
#include <lsst/afw/math/ConvolveImage.h>

#include <Eigen/Cholesky>
#include <Eigen/Core>
#include <Eigen/LU>
#include <Eigen/QR>

#include <lsst/pex/policy/Policy.h>
%}

/******************************************************************************/

%include "lsst/p_lsstSwig.i"
%include "lsst/daf/base/persistenceMacros.i"
%import  "lsst/afw/image/imageLib.i" 
%import  "lsst/afw/detection/detectionLib.i"
%import  "lsst/afw/math/kernelLib.i"

/* so SWIG knows that PolynomialFunction2D is derived from Function2 */
%import  "lsst/afw/math/functionLib.i"  

%lsst_exceptions();

%pythoncode %{
import lsst.utils

def version(HeadURL = r"$HeadURL$"):
    """Return a version given a HeadURL string. If a different version is setup, return that too"""

    version_svn = lsst.utils.guessSvnVersion(HeadURL)

    try:
        import eups
    except ImportError:
        return version_svn
    else:
        try:
            version_eups = eups.setup("ip_diffim")
        except AttributeError:
            return version_svn

    if version_eups == version_svn:
        return version_svn
    else:
        return "%s (setup: %s)" % (version_svn, version_eups)

%}

%{
#include "lsst/ip/diffim/ImageSubtract.h"
%}

SWIG_SHARED_PTR(PsfMatchingFunctorF, lsst::ip::diffim::PsfMatchingFunctor<float>);
SWIG_SHARED_PTR(PsfMatchingFunctorD, lsst::ip::diffim::PsfMatchingFunctor<double>);

%include "lsst/ip/diffim/ImageSubtract.h"

%template(PsfMatchingFunctorF)
    lsst::ip::diffim::PsfMatchingFunctor<float>;
%template(PsfMatchingFunctorD)
    lsst::ip::diffim::PsfMatchingFunctor<double>;

%template(FindSetBitsU)
    lsst::ip::diffim::FindSetBits<lsst::afw::image::Mask<lsst::afw::image::MaskPixel> >;

%template(FindCountsI)
    lsst::ip::diffim::FindCounts<int>;
%template(FindCountsF)
    lsst::ip::diffim::FindCounts<float>;
%template(FindCountsD)
    lsst::ip::diffim::FindCounts<double>;

%template(ImageStatisticsI)
    lsst::ip::diffim::ImageStatistics<int>;
%template(ImageStatisticsF)
    lsst::ip::diffim::ImageStatistics<float>;
%template(ImageStatisticsD)
    lsst::ip::diffim::ImageStatistics<double>;

%define %convolveAndSubtract(PIXEL_T)
   %template(convolveAndSubtract)
       lsst::ip::diffim::convolveAndSubtract<PIXEL_T, double>;
   %template(convolveAndSubtract)
       lsst::ip::diffim::convolveAndSubtract<PIXEL_T, lsst::afw::math::Function2<double> const&>;
%enddef

%convolveAndSubtract(float);
#if 0
%convolveAndSubtract(double);           // image subtraction on double images??!?  Not instantiated in .cc either
#endif

%template(getCollectionOfFootprintsForPsfMatching)
    lsst::ip::diffim::getCollectionOfFootprintsForPsfMatching<float>;
%template(getCollectionOfFootprintsForPsfMatching)
    lsst::ip::diffim::getCollectionOfFootprintsForPsfMatching<double>;

/******************************************************************************/

%{
#include "lsst/ip/diffim/SpatialModelKernel.h"
%}

SWIG_SHARED_PTR(SpatialModelKernelF, lsst::ip::diffim::SpatialModelKernel<float>);
SWIG_SHARED_PTR(SpatialModelKernelD, lsst::ip::diffim::SpatialModelKernel<double>);

%include "lsst/ip/diffim/SpatialModelKernel.h"

%template(SpatialModelKernelF) lsst::ip::diffim::SpatialModelKernel<float>;
%template(SpatialModelKernelD) lsst::ip::diffim::SpatialModelKernel<double>;

%template(VectorSpatialModelKernelF) std::vector<lsst::ip::diffim::SpatialModelKernel<float>::Ptr >;
%template(VectorSpatialModelKernelD) std::vector<lsst::ip::diffim::SpatialModelKernel<double>::Ptr >;


/******************************************************************************/

%{
#include "lsst/ip/diffim/SpatialModelCell.h"
%}

SWIG_SHARED_PTR(SpatialModelCellF, lsst::ip::diffim::SpatialModelCell<lsst::ip::diffim::SpatialModelKernel<float> >);
SWIG_SHARED_PTR(SpatialModelCellD, lsst::ip::diffim::SpatialModelCell<lsst::ip::diffim::SpatialModelKernel<double> >);

%include "lsst/ip/diffim/SpatialModelCell.h"

%template(SpatialModelCellF) lsst::ip::diffim::SpatialModelCell<lsst::ip::diffim::SpatialModelKernel<float> >;
%template(SpatialModelCellD) lsst::ip::diffim::SpatialModelCell<lsst::ip::diffim::SpatialModelKernel<double> >;

%template(VectorSpatialModelCellF) std::vector<lsst::ip::diffim::SpatialModelCell<lsst::ip::diffim::SpatialModelKernel<float> >::Ptr >;
%template(VectorSpatialModelCellD) std::vector<lsst::ip::diffim::SpatialModelCell<lsst::ip::diffim::SpatialModelKernel<double> >::Ptr >;

/******************************************************************************/

