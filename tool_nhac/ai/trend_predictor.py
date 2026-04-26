from loguru import logger
import numpy as np

class TrendPredictor:
    """Phân tích Linear Regression để dự đoán trend dựa vào log view theo ngày."""
    
    def predict_growth(self, historical_usage: list[int]) -> float:
        """
        historical_usage: List of usage counts theo timeline 
        Trả về hệ số góc (slope) - Tốc độ tăng trưởng.
        """
        if len(historical_usage) < 2:
            return 0.0
            
        try:
            from sklearn.linear_model import LinearRegression
            
            X = np.arange(len(historical_usage)).reshape(-1, 1)
            y = np.array(historical_usage)
            
            model = LinearRegression()
            model.fit(X, y)
            
            slope = model.coef_[0]
            return float(slope)
        except ImportError:
            logger.error("Vui lòng cài đặt: pip install scikit-learn")
            return 0.0
        except Exception as e:
            logger.warning(f"Error predicting trend: {e}")
            return 0.0
