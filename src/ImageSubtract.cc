// -*- lsst-c++ -*-
/**
 * @file
 *
 * @brief Implementation of image subtraction functions declared in ImageSubtract.h
 *
 * @author Andrew Becker, University of Washington
 *
 * @ingroup ip_diffim
 */
#include <iostream>
#include <limits>
#include <boost/timer.hpp> 

#include <Eigen/Cholesky>
#include <Eigen/Core>
#include <Eigen/LU>
#include <Eigen/QR>

// NOTE -  trace statements >= 6 can ENTIRELY kill the run time
// #define LSST_MAX_TRACE 5

#include <lsst/ip/diffim/ImageSubtract.h>
#include <lsst/afw/image.h>
#include <lsst/afw/math.h>
#include <lsst/pex/exceptions/Exception.h>
#include <lsst/pex/logging/Trace.h>
#include <lsst/pex/logging/Log.h>
#include <lsst/afw/detection/Footprint.h>
#include <lsst/afw/math/ConvolveImage.h>

#define DEBUG_MATRIX 0

namespace exceptions = lsst::pex::exceptions; 
namespace logging    = lsst::pex::logging; 
namespace image      = lsst::afw::image;
namespace math       = lsst::afw::math;
namespace diffim     = lsst::ip::diffim;

//
// Constructors
//
template <typename ImageT, typename VarT>
diffim::PsfMatchingFunctor<ImageT, VarT>::PsfMatchingFunctor(
        lsst::afw::math::KernelList<lsst::afw::math::Kernel> const& basisList
    ) :
    _basisList(basisList),
    _background(0.),
    _backgroundError(0.),
    _kernel(boost::shared_ptr<lsst::afw::math::Kernel>()),
    _kernelError(boost::shared_ptr<lsst::afw::math::Kernel>())
{;}

//
// Public Member Functions
//

template <typename ImageT, typename VarT>
void diffim::PsfMatchingFunctor<ImageT, VarT>::reset() {
    /* HEY , FOR SOME REASON THE KERNEL RESET DOES NOT WORK AND SEG FAULTS */
    //this->_background      = 0.;
    //this->_backgroundError = 0.;
    //this->_kernel.reset();
    //this->_kernelError.reset();
}

/** Create PSF matching kernel
 */
template <typename ImageT, typename VarT>
void diffim::PsfMatchingFunctor<ImageT, VarT>::apply(
    lsst::afw::image::Image<ImageT> const& imageToConvolve,    //!< Image to apply kernel to
    lsst::afw::image::Image<ImageT> const& imageToNotConvolve, //!< Image whose PSF you want to match to
    lsst::afw::image::Image<VarT>   const& varianceEstimate,   //!< Estimate of the variance per pixel
    lsst::pex::policy::Policy  const& policy            //!< Policy file
    ) {
    
    // Make sure you do not overwrite anyone else's kernels
    this->reset();

    int const kCols = policy.getInt("kernelCols");
    int const kRows = policy.getInt("kernelRows");
    
    int const nKernelParameters     = this->_basisList.size();
    int const nBackgroundParameters = 1;
    int const nParameters           = nKernelParameters + nBackgroundParameters;
    
    boost::timer t;
    t.restart();
    
    Eigen::MatrixXd M = Eigen::MatrixXd::Zero(nParameters, nParameters);
    Eigen::VectorXd B = Eigen::VectorXd::Zero(nParameters);
    
    std::vector<boost::shared_ptr<image::Image<ImageT> > > convolvedImageList(nKernelParameters);
    typename std::vector<boost::shared_ptr<image::Image<ImageT> > >::iterator citer = convolvedImageList.begin();
    std::vector<boost::shared_ptr<math::Kernel> >::const_iterator kiter = this->_basisList.begin();
    
    // Create C_ij in the formalism of Alard & Lupton */
    for (; kiter != this->_basisList.end(); ++kiter, ++citer) {
        /*
         * NOTE : we could also *precompute* the entire template image convolved with these functions
         *        and save them somewhere to avoid this step each time.  however, our paradigm is to
         *        compute whatever is needed on the fly.  hence this step here.
         */
        *citer = typename image::Image<ImageT>::Ptr(new image::Image<ImageT>(imageToConvolve.getDimensions()));
        math::convolve(**citer, imageToConvolve, **kiter, false);
    } 

    double time = t.elapsed();
    logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                       "Total compute time to do basis convolutions : %.2f s", time);
    t.restart();
     
    kiter = this->_basisList.begin();
    citer = convolvedImageList.begin();

    // Ignore buffers around edge of convolved images :
    //
    // If the kernel has width 5, it has center pixel 2.  The first good pixel
    // is the (5-2)=3rd pixel, which is array index 2, and ends up being the
    // index of the central pixel.
    //
    // You also have a buffer of unusable pixels on the other side, numbered
    // width-center-1.  The last good usable pixel is N-width+center+1.

    // Example : the kernel is width = 5, center = 2
    //
    //     ---|---|-c-|---|---|
    //          
    //           the image is width = N
    //           convolve this with the kernel, and you get
    //
    //    |-x-|-x-|-g-|---|---| ... |---|---|-g-|-x-|-x-|
    //
    //           g = first/last good pixel
    //           x = bad
    // 
    //           the first good pixel is the array index that has the value "center", 2
    //           the last good pixel has array index N-(5-2)+1
    //           eg. if N = 100, you want to use up to index 97
    //               100-3+1 = 98, and the loops use i < 98, meaning the last
    //               index you address is 97.
   
    int const startCol = (*kiter)->getCtrX();
    int const startRow = (*kiter)->getCtrY();
    int const endCol   = (*citer)->getWidth()  - ((*kiter)->getWidth()  - (*kiter)->getCtrX()) + 1;
    int const endRow   = (*citer)->getHeight() - ((*kiter)->getHeight() - (*kiter)->getCtrY()) + 1;

    std::vector<typename image::Image<ImageT>::xy_locator> convolvedLocatorList;
    for (citer = convolvedImageList.begin(); citer != convolvedImageList.end(); ++citer) {
        convolvedLocatorList.push_back( (**citer).xy_at(startCol,startRow) );
    }
    typename image::Image<ImageT>::xy_locator imageToConvolveLocator    = imageToConvolve.xy_at(startCol, startRow);
    typename image::Image<ImageT>::xy_locator imageToNotConvolveLocator = imageToNotConvolve.xy_at(startCol, startRow);
    xyi_locator varianceLocator                                         = varianceEstimate.xy_at(startCol, startRow);

    // Unit test ImageSubtract_1.py should show
    // Image range : 9 9 -> 31 31 : 2804.000000 2798.191162
    logging::TTrace<8>("lsst.ip.diffim.PsfMatchingFunctor.apply",
                       "Image range : %d %d -> %d %d : %f %f %f",
                       startCol, startRow, endCol, endRow, 
                       0 + *imageToConvolveLocator, 0 + *imageToNotConvolveLocator, 0 + *varianceLocator);

    std::pair<int, int> rowStep = std::make_pair(static_cast<int>(-(endCol-startCol)), 1);
    for (int row = startRow; row < endRow; ++row) {
        for (int col = startCol; col < endCol; ++col) {
            ImageT const ncImage          = *imageToNotConvolveLocator;
            double const iVariance        = 1.0 / *varianceLocator;
            
            // kernel index i
            typename std::vector<typename image::Image<ImageT>::xy_locator>::iterator citeri = convolvedLocatorList.begin();
            typename std::vector<typename image::Image<ImageT>::xy_locator>::iterator citerE = convolvedLocatorList.end();
            for (int kidxi = 0; citeri != citerE; ++citeri, ++kidxi) {
                ImageT const cdImagei = **citeri;
                
                // kernel index j
                typename std::vector<typename image::Image<ImageT>::xy_locator>::iterator citerj = citeri;
                for (int kidxj = kidxi; citerj != citerE; ++citerj, ++kidxj) {
                    ImageT const cdImagej  = **citerj;
                    M(kidxi, kidxj) += cdImagei*cdImagej*iVariance;
		    
		    /*
		    logging::TTrace<8>("lsst.ip.diffim.PsfMatchingFunctor.apply",
				       "%f %f %f",
				       ncImage, cdImagei, cdImagej, 1./iVariance);
		    */
                } 
                
                B(kidxi) += ncImage*cdImagei*iVariance;
                
                // Constant background term; effectively j = kidxj + 1 */
                M(kidxi, nParameters-1) += cdImagei*iVariance;
            } 
            
            // Background term; effectively i = kidxi + 1 
            B(nParameters-1)                += ncImage*iVariance;
            M(nParameters-1, nParameters-1) += 1.0*iVariance;
            
            // Step each accessor in column
            ++imageToConvolveLocator.x();
            ++imageToNotConvolveLocator.x();
            ++varianceLocator.x();
            for (int ki = 0; ki < nKernelParameters; ++ki) {
                ++convolvedLocatorList[ki].x();
            }             
            
        } // col
        
        // Get to next row, first col
        imageToConvolveLocator    += rowStep;
        imageToNotConvolveLocator += rowStep;
        varianceLocator           += rowStep;
        for (int ki = 0; ki < nKernelParameters; ++ki) {
            convolvedLocatorList[ki] += rowStep;
        }
        
    } // row
    
    /** @note If we are going to regularize the solution to M, this is the place
     * to do it 
     *
     * This does not seem to change things much...
     */

    /*
    for (int kidxi=0; kidxi < nKernelParameters; ++kidxi) {
        int kiPosx     = kidxi % kCols;
        int kiPosy     = kidxi / kCols;
	double kiDist2 = (kiPosx-kCols/2)*(kiPosx-kCols/2) + (kiPosy-kRows/2)*(kiPosy-kRows/2);

        for (int kidxj=kidxi; kidxj < nKernelParameters; ++kidxj) {
	    int kjPosx     = kidxj % kCols;
	    int kjPosy     = kidxj / kCols;
	    double kjDist2 = (kjPosx-kCols/2)*(kjPosx-kCols/2) + (kjPosy-kRows/2)*(kjPosy-kRows/2);

	    //std::cout << kidxi << " " << kidxj << " " << kiDist2 << " " << kjDist2 << std::endl;
	    M(kidxi, kidxj) += kiDist2*kjDist2;
        }
    }
    */

    // Fill in rest of M
    for (int kidxi=0; kidxi < nParameters; ++kidxi) {
        for (int kidxj=kidxi+1; kidxj < nParameters; ++kidxj) {
            M(kidxj, kidxi) = M(kidxi, kidxj);
        }
    }
    
    time = t.elapsed();
    logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                       "Total compute time to step through pixels : %.2f s", time);
    t.restart();

    //std::cout << "B eigen : " << B << std::endl;

    // To use Cholesky decomposition, the matrix needs to be symmetric (M is, by
    // design) and positive definite.  
    //
    // Eventually put a check in here to make sure its positive definite
    //
    Eigen::VectorXd Soln = Eigen::VectorXd::Zero(nParameters);;
    if (!( M.ldlt().solve(B, &Soln) )) {
        logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                           "Unable to determine kernel via Cholesky LDL^T");
        if (!( M.llt().solve(B, &Soln) )) {
            logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                               "Unable to determine kernel via Cholesky LL^T");
            if (!( M.lu().solve(B, &Soln) )) {
                logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                                   "Unable to determine kernel via LU");
                // LAST RESORT
                try {
                    Eigen::SelfAdjointEigenSolver<Eigen::MatrixXd> eVecValues(M);
                    Eigen::MatrixXd const& R = eVecValues.eigenvectors();
                    Eigen::VectorXd eValues  = eVecValues.eigenvalues();
                    
                    for (int i = 0; i != eValues.rows(); ++i) {
                        if (eValues(i) != 0.0) {
                            eValues(i) = 1.0/eValues(i);
                        }
                    }
                    
                    Soln = R*eValues.asDiagonal()*R.transpose()*B;
                } catch (exceptions::Exception& e) {
                    logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                                       "Unable to determine kernel via eigen-values");
                    
                    throw LSST_EXCEPT(exceptions::Exception, "Unable to determine kernel solution in PsfMatchingFunctor::apply");
                }
            }
        }
    }
    //std::cout << "Soln eigen : " << Soln << std::endl;
    //return;

    // Estimate of parameter uncertainties comes from the inverse of the
    // covariance matrix (noise spectrum).  
    // N.R. 15.4.8 to 15.4.15
    // 
    // Since this is a linear problem no need to use Fisher matrix
    // N.R. 15.5.8

    // Although I might be able to take advantage of the solution above.
    // Since this now works and is not the rate limiting step, keep as-is for DC3a.

    // Use Cholesky decomposition again.
    // Cholkesy:
    // Cov       =  L L^t
    // Cov^(-1)  = (L L^t)^(-1)
    //           = (L^T)^-1 L^(-1)
    Eigen::MatrixXd             Cov    = M.transpose() * M;
    Eigen::LLT<Eigen::MatrixXd> llt    = Cov.llt();
    Eigen::MatrixXd             Error2 = llt.matrixL().transpose().inverse() * llt.matrixL().inverse();
    
    time = t.elapsed();
    logging::TTrace<5>("lsst.ip.diffim.PsfMatchingFunctor.apply", 
                       "Total compute time to do matrix math : %.2f s", time);
    
    // Translate from Eigen vectors into LSST classes
    std::vector<double> kValues(kCols*kRows);
    std::vector<double> kErrValues(kCols*kRows);
    for (int row = 0, idx = 0; row < kRows; row++) {
        for (int col = 0; col < kCols; col++, idx++) {
            
            // Insanity checking
            if (std::isnan( Soln(idx) )) {
                throw LSST_EXCEPT(exceptions::Exception, 
                                  str(boost::format("Unable to determine kernel solution %d (nan)") % idx));
            }
            if (std::isnan( Error2(idx, idx) )) {
                throw LSST_EXCEPT(exceptions::Exception, 
                                  str(boost::format("Unable to determine kernel uncertainty %d (nan)") % idx));
            }
            if (Error2(idx, idx) < 0.0) {
                throw LSST_EXCEPT(exceptions::Exception,
                                  str(boost::format("Unable to determine kernel uncertainty, negative variance %d (%.3e)") % 
                                      idx % Error2(idx, idx)));
            }
            
            kValues[idx]    = Soln(idx);
            kErrValues[idx] = sqrt(Error2(idx, idx));
        }
    }
    this->_kernel.reset( new math::LinearCombinationKernel(this->_basisList, kValues) );
    this->_kernelError.reset( new math::LinearCombinationKernel(this->_basisList, kErrValues) );
    
    // Estimate of Background and Background Error */
    if (std::isnan( Error2(nParameters-1, nParameters-1) )) {
        throw LSST_EXCEPT(exceptions::Exception, "Unable to determine background uncertainty (nan)");
    }
    if (Error2(nParameters-1, nParameters-1) < 0.0) {
        throw LSST_EXCEPT(exceptions::Exception, 
                          str(boost::format("Unable to determine background uncertainty, negative variance (%.3e)") % 
                              Error2(nParameters-1, nParameters-1) 
                              ));
    }
    this->_background      = Soln(nParameters-1);
    this->_backgroundError = sqrt(Error2(nParameters-1, nParameters-1));
}


//
// Subroutines
//

/** 
 * @brief Generate a basis set of delta function Kernels.
 *
 * Generates a vector of Kernels sized nCols * nRows, where each Kernel has
 * a unique pixel set to value 1.0 with the other pixels valued 0.0.  This
 * is the "delta function" basis set.
 * 
 * @return Vector of orthonormal delta function Kernels.
 *
 * @throw lsst::pex::exceptions::DomainError if nRows or nCols not positive
 *
 * @ingroup diffim
 */
math::KernelList<math::Kernel>
diffim::generateDeltaFunctionKernelSet(
    unsigned int width,                 ///< number of columns in the set
    unsigned int height                 ///< number of rows in the set
    ) {
    if ((width < 1) || (height < 1)) {
        throw LSST_EXCEPT(exceptions::Exception, "nRows and nCols must be positive");
    }
    const int signedWidth = static_cast<int>(width);
    const int signedHeight = static_cast<int>(height);
    math::KernelList<math::Kernel> kernelBasisList;
    for (int row = 0; row < signedHeight; ++row) {
        for (int col = 0; col < signedWidth; ++col) {
            boost::shared_ptr<math::Kernel> 
                kernelPtr( new math::DeltaFunctionKernel(width, height, image::PointI(col,row) ) );
            kernelBasisList.push_back(kernelPtr);
        }
    }
    return kernelBasisList;
}

/** 
 * @brief Generate an Alard-Lupton basis set of Kernels.
 *
 * Not implemented.
 * 
 * @return Vector of Alard-Lupton Kernels.
 *
 * @throw lsst::pex::exceptions::DomainError if nRows or nCols not positive
 * @throw lsst::pex::exceptions::DomainError until implemented
 *
 * @ingroup diffim
 */
math::KernelList<math::Kernel>
diffim::generateAlardLuptonKernelSet(
    unsigned int nRows, 
    unsigned int nCols, 
    std::vector<double> const& sigGauss, 
    std::vector<double> const& degGauss  
    ) {
    if ((nCols < 1) || (nRows < 1)) {
        throw LSST_EXCEPT(exceptions::Exception, "nRows and nCols must be positive");
    }
    throw LSST_EXCEPT(exceptions::Exception, "Not implemented");
    
    math::KernelList<math::Kernel> kernelBasisList;
    return kernelBasisList;
}

/************************************************************************************************************/
/*
 * Adds a Function to an Image
 *
 * @note MAJOR NOTE; I need to check if my scaling of the image range from -1 to
 * 1 gets messed up here.  ACB.
 *
 * @note This routine assumes that the pixel coordinates start at (0, 0) which is
 * in general not true
 *
 * @node this function was renamed from addFunctionToImage to addSomethingToImage to allow generic programming
 */
namespace {
    template <typename ImageT, typename FunctionT>
    void addSomethingToImage(ImageT &image,
                             FunctionT const& function
                            ) {

        // Set the pixels row by row, to avoid repeated checks for end-of-row
        for (int y = 0; y != image.getHeight(); ++y) {
            double yPos = image::positionToIndex(y);
        
            double xPos = image::positionToIndex(0);
            for (typename ImageT::x_iterator ptr = image.row_begin(y), end = image.row_end(y);
                 ptr != end; ++ptr, ++xPos) {            
                *ptr += function(xPos, yPos);
            }
        }
    }
    //
    // Add a scalar.
    //
    template <typename ImageT>
    void addSomethingToImage(image::Image<ImageT> &image,
                             double value
                            ) {
        if (value != 0.0) {
            image += value;
        }
    }
}

/** 
 * @brief Implement fundamental difference imaging step of convolution and
 * subtraction : D = I - (K*T + bg) where * denotes convolution
 * 
 * @note If you convolve the science image, D = (K*I + bg) - T, set invert=False
 *
 * @note The template is taken to be an MaskedImage; this takes c 1.6 times as long
 * as using an Image
 *
 * @return Difference image
 *
 * @ingroup diffim
 */
template <typename ImageT, typename BackgroundT>
image::MaskedImage<ImageT> diffim::convolveAndSubtract(
    lsst::afw::image::MaskedImage<ImageT> const& imageToConvolve,    ///< Image T to convolve with Kernel
    lsst::afw::image::MaskedImage<ImageT> const& imageToNotConvolve, ///< Image I to subtract convolved template from
    lsst::afw::math::Kernel const& convolutionKernel,                ///< PSF-matching Kernel used for convolution
    BackgroundT background,                               ///< Differential background function or scalar
    bool invert                                           ///< Invert the output difference image
    ) {

    logging::TTrace<8>("lsst.ip.diffim.convolveAndSubtract", "Convolving using convolve");
    
    image::MaskedImage<ImageT> convolvedMaskedImage(imageToConvolve.getDimensions());
    convolvedMaskedImage.setXY0(imageToConvolve.getXY0());
    math::convolve(convolvedMaskedImage, imageToConvolve, convolutionKernel, false);
    
    /* Add in background */
    addSomethingToImage(*(convolvedMaskedImage.getImage()), background);
    
    /* Do actual subtraction */
    convolvedMaskedImage -= imageToNotConvolve;

    /* Invert */
    if (invert) {
        convolvedMaskedImage *= -1.0;
    }

    return convolvedMaskedImage;
}

/** 
 * @brief Implement fundamental difference imaging step of convolution and
 * subtraction : D = I - (K.x.T + bg)
 *
 * @note The template is taken to be an Image, not a MaskedImage; it therefore
 * has neither variance nor bad pixels
 *
 * @note If you convolve the science image, D = (K*I + bg) - T, set invert=False
 * 
 * @return Difference image
 *
 * @ingroup diffim
 */
template <typename ImageT, typename BackgroundT>
image::MaskedImage<ImageT> diffim::convolveAndSubtract(
    lsst::afw::image::Image<ImageT> const& imageToConvolve,          ///< Image T to convolve with Kernel
    lsst::afw::image::MaskedImage<ImageT> const& imageToNotConvolve, ///< Image I to subtract convolved template from
    lsst::afw::math::Kernel const& convolutionKernel,                ///< PSF-matching Kernel used for convolution
    BackgroundT background,                                          ///< Differential background function or scalar
    bool invert                                                      ///< Invert the output difference image
    ) {
    
    logging::TTrace<8>("lsst.ip.diffim.convolveAndSubtract", "Convolving using convolve");
    
    image::MaskedImage<ImageT> convolvedMaskedImage(imageToConvolve.getDimensions());
    convolvedMaskedImage.setXY0(imageToConvolve.getXY0());
    
    math::convolve(*convolvedMaskedImage.getImage(), imageToConvolve, convolutionKernel, false);
    
    /* Add in background */
    addSomethingToImage(*convolvedMaskedImage.getImage(), background);
    
    /* Do actual subtraction */
    *convolvedMaskedImage.getImage() -= *imageToNotConvolve.getImage();

    /* Invert */
    if (invert) {
        *convolvedMaskedImage.getImage() *= -1.0;
    }
    *convolvedMaskedImage.getMask() <<= *imageToNotConvolve.getMask();
    *convolvedMaskedImage.getVariance() <<= *imageToNotConvolve.getVariance();
    
    return convolvedMaskedImage;
}

/** 
 * @brief Runs Detection on a single image for significant peaks, and checks
 * returned Footprints for Masked pixels.
 *
 * Accepts two MaskedImages, one of which is to be convolved to match the
 * other.  The Detection package is run on the image to be convolved
 * (assumed to be higher S/N than the other image).  The subimages
 * associated with each returned Footprint in both images are checked for
 * Masked pixels; Footprints containing Masked pixels are rejected.  The
 * Footprints are grown by an amount specified in the Policy.  The
 * acceptible Footprints are returned in a vector.
 *
 * @return Vector of "clean" Footprints around which Image Subtraction
 * Kernels will be built.
 *
 * @ingroup diffim
 */
template <typename ImageT>
std::vector<lsst::afw::detection::Footprint::Ptr> diffim::getCollectionOfFootprintsForPsfMatching(
    lsst::afw::image::MaskedImage<ImageT> const& imageToConvolve,    
    lsst::afw::image::MaskedImage<ImageT> const& imageToNotConvolve, 
    lsst::pex::policy::Policy  const& policy                                       
    ) {
    
    // Parse the Policy
    unsigned int fpNpixMin      = policy.getInt("fpNpixMin");
    unsigned int fpNpixMax      = policy.getInt("fpNpixMax");

    int const kCols             = policy.getInt("kernelCols");
    int const kRows             = policy.getInt("kernelRows");
    double fpGrowKsize          = policy.getDouble("fpGrowKsize");

    int minCleanFp              = policy.getInt("minCleanFp");
    double detThreshold         = policy.getDouble("detThreshold");
    double detThresholdScaling  = policy.getDouble("detThresholdScaling");
    double detThresholdMin      = policy.getDouble("detThresholdMin");
    std::string detThresholdType = policy.getString("detThresholdType");

    // Number of pixels to grow each Footprint, based upon the Kernel size
    int fpGrowPix = int(fpGrowKsize * ( (kCols > kRows) ? kCols : kRows ));

    // Grab mask bits from the image to convolve, since that is what we'll be operating on
    // Overridden now that we use the FootprintFunctor to look for any masked pixels
    // int badMaskBit = imageToConvolve.getMask()->getMaskPlane("BAD");
    // image::MaskPixel badPixelMask = (badMaskBit < 0) ? 0 : (1 << badMaskBit);
    
    // List of Footprints
    std::vector<lsst::afw::detection::Footprint::Ptr> footprintListIn;
    std::vector<lsst::afw::detection::Footprint::Ptr> footprintListOut;

    // Functors to search through the images for bad pixels within candidate footprints
    diffim::FindSetBits<image::Mask<image::MaskPixel> > itcFunctor(*(imageToConvolve.getMask())); 
    diffim::FindSetBits<image::Mask<image::MaskPixel> > itncFunctor(*(imageToNotConvolve.getMask())); 
 
    int nCleanFp = 0;
    while ( (nCleanFp < minCleanFp) and (detThreshold > detThresholdMin) ) {
        footprintListIn.clear();
        footprintListOut.clear();
        
        // Find detections
        lsst::afw::detection::Threshold threshold = 
                lsst::afw::detection::createThreshold(detThreshold, detThresholdType);
        lsst::afw::detection::DetectionSet<ImageT> detectionSet(
                imageToConvolve, 
                threshold,
                "",
                fpNpixMin);
        
        // Get the associated footprints
        footprintListIn = detectionSet.getFootprints();
        logging::TTrace<4>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                           "Found %d total footprints above threshold %.3f",
                           footprintListIn.size(), detThreshold);

        // Iterate over footprints, look for "good" ones
        nCleanFp = 0;
        for (std::vector<lsst::afw::detection::Footprint::Ptr>::iterator i = footprintListIn.begin(); i != footprintListIn.end(); ++i) {
            // footprint has too many pixels
            if (static_cast<unsigned int>((*i)->getNpix()) > fpNpixMax) {
                logging::TTrace<5>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                               "Footprint has too many pix: %d (max =%d)", 
                               (*i)->getNpix(), fpNpixMax);
                continue;
            } 
            
            logging::TTrace<8>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                               "Footprint in : %d,%d -> %d,%d",
                               (*i)->getBBox().getX0(), (*i)->getBBox().getX1(), 
                               (*i)->getBBox().getY0(), (*i)->getBBox().getY1());

            logging::TTrace<8>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                               "Grow by : %d pixels", fpGrowPix);

            // Grow the footprint
            // true = isotropic grow = slow
            // false = 'manhattan grow' = fast
            lsst::afw::detection::Footprint::Ptr fpGrow = 
                lsst::afw::detection::growFootprint(*i, fpGrowPix, false);
            
            logging::TTrace<6>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                               "Footprint out : %d,%d -> %d,%d (center %d,%d)",
                               (*fpGrow).getBBox().getX0(), (*fpGrow).getBBox().getY0(),
			       (*fpGrow).getBBox().getX1(), (*fpGrow).getBBox().getY1(),
			       int( 0.5 * ((*i)->getBBox().getX0()+(*i)->getBBox().getX1()) ),
			       int( 0.5 * ((*i)->getBBox().getY0()+(*i)->getBBox().getY1()) ) );


            // Grab a subimage; there is an exception if it's e.g. too close to the image */
            try {
                image::BBox fpBBox = (*fpGrow).getBBox();
                fpBBox.shift(-imageToConvolve.getX0(), -imageToConvolve.getY0());
                
                image::MaskedImage<ImageT> subImageToConvolve(imageToConvolve, fpBBox);
                image::MaskedImage<ImageT> subImageToNotConvolve(imageToNotConvolve, fpBBox);
            } catch (exceptions::Exception& e) {
                logging::TTrace<4>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching",
                                   "Exception caught extracting Footprint");
                logging::TTrace<5>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching",
                                   e.what());
                continue;
            }

            // Search for bad pixels within the footprint
            itcFunctor.apply(*fpGrow);
            if (itcFunctor.getBits() > 0) {
                logging::TTrace<5>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                               "Footprint has bad pix in image to convolve"); 
                continue;
            }

            itncFunctor.apply(*fpGrow);
            if (itncFunctor.getBits() > 0) {
                logging::TTrace<5>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                               "Footprint has bad pix in image not to convolve");
                continue;
            }

            // If we get this far, we have a clean footprint
            footprintListOut.push_back(fpGrow);
            nCleanFp += 1;
        }
        
        detThreshold *= detThresholdScaling;
    }
    if (footprintListOut.size() == 0) {
      throw LSST_EXCEPT(exceptions::Exception, 
			"Unable to find any footprints for Psf matching");
    }

    logging::TTrace<3>("lsst.ip.diffim.getCollectionOfFootprintsForPsfMatching", 
                       "Found %d clean footprints above threshold %.3f",
                       footprintListOut.size(), detThreshold/detThresholdScaling);
    
    return footprintListOut;
}

// Explicit instantiations
// \cond

template class diffim::PsfMatchingFunctor<float, float>;
template class diffim::PsfMatchingFunctor<double, float>;

template class diffim::FindSetBits<image::Mask<> >;

template class diffim::FindCounts<float>;
template class diffim::FindCounts<double>;

template class diffim::ImageStatistics<float>;
template class diffim::ImageStatistics<double>;

/* */

#define p_INSTANTIATE_convolveAndSubtract(TEMPLATE_IMAGE_T, TYPE)     \
    template \
    image::MaskedImage<TYPE> diffim::convolveAndSubtract( \
        image::TEMPLATE_IMAGE_T<TYPE> const& imageToConvolve, \
        image::MaskedImage<TYPE> const& imageToNotConvolve, \
        math::Kernel const& convolutionKernel, \
        double background, \
        bool invert);      \
    \
    template \
    image::MaskedImage<TYPE> diffim::convolveAndSubtract( \
        image::TEMPLATE_IMAGE_T<TYPE> const& imageToConvolve, \
        image::MaskedImage<TYPE> const& imageToNotConvolve, \
        math::Kernel const& convolutionKernel, \
        math::Function2<double> const& backgroundFunction, \
        bool invert); \

#define INSTANTIATE_convolveAndSubtract(TYPE) \
p_INSTANTIATE_convolveAndSubtract(Image, TYPE) \
p_INSTANTIATE_convolveAndSubtract(MaskedImage, TYPE)
/*
 * Here are the instantiations.
 *
 * Do we really need double diffim code?  It isn't sufficient to remove it here; you'll have to also remove at
 * least SpatialModelKernel<double> and swig instantiations thereof
 */
INSTANTIATE_convolveAndSubtract(float);
INSTANTIATE_convolveAndSubtract(double);

/* */

template
std::vector<lsst::afw::detection::Footprint::Ptr> diffim::getCollectionOfFootprintsForPsfMatching(
    image::MaskedImage<float> const& imageToConvolve,
    image::MaskedImage<float> const& imageToNotConvolve,
    lsst::pex::policy::Policy const& policy);

template
std::vector<lsst::afw::detection::Footprint::Ptr> diffim::getCollectionOfFootprintsForPsfMatching(
    image::MaskedImage<double> const& imageToConvolve,
    image::MaskedImage<double> const& imageToNotConvolve,
    lsst::pex::policy::Policy  const& policy);

