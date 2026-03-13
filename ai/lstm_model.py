"""
Pinger v2.0 - LSTM Neural Network Tahmin Modülü

Mimari:
  - Stacked BiLSTM (çift yönlü, daha iyi bağlam)
  - Attention mekanizması (hangi bar'ların önemli olduğunu öğrenir)
  - Dropout regularizasyon
  - Binary classification: fiyat artacak mı? (softmax → olasılık)

Kullanım:
  trainer = LSTMTrainer(config)
  trainer.train(symbol, exchange)       # Eğit ve kaydet
  prob = trainer.predict(symbol, exchange)  # 0.0-1.0 arası olasılık
"""

import os
import time
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Dict

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset, random_split
from sklearn.preprocessing import StandardScaler

from ai.data_fetcher import OHLCVFetcher, prepare_sequences, normalize_sequence, FEATURE_COLS
from utils.logger import setup_logger

logger = setup_logger(__name__)

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# GPU varsa kullan
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
logger.info(f"LSTM device: {DEVICE}")


# ======================================================================
# Model Mimarisi
# ======================================================================

class AttentionLayer(nn.Module):
    """
    Temporal attention: hangi zaman adımlarının önemli olduğunu öğrenir.
    LSTM output üzerine uygulanır.
    """

    def __init__(self, hidden_size: int):
        super().__init__()
        self.attention = nn.Linear(hidden_size * 2, 1)  # *2 çünkü BiLSTM

    def forward(self, lstm_out: torch.Tensor) -> torch.Tensor:
        # lstm_out: (batch, seq_len, hidden*2)
        scores = self.attention(lstm_out)          # (batch, seq_len, 1)
        weights = torch.softmax(scores, dim=1)     # (batch, seq_len, 1)
        context = (lstm_out * weights).sum(dim=1)  # (batch, hidden*2)
        return context


class PingerLSTM(nn.Module):
    """
    Stacked Bidirectional LSTM + Attention + Classifier

    Input:  (batch, seq_len, n_features)
    Output: (batch, 1) — sigmoid olasılığı (fiyat artacak mı?)
    """

    def __init__(
        self,
        n_features: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.2,
    ):
        super().__init__()

        self.n_features = n_features
        self.hidden_size = hidden_size

        # Input normalization
        self.input_norm = nn.LayerNorm(n_features)

        # Bidirectional LSTM
        self.lstm = nn.LSTM(
            input_size=n_features,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
            bidirectional=True,
        )

        # Attention
        self.attention = AttentionLayer(hidden_size)

        # Classifier head
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size * 2, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(dropout / 2),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, n_features)
        x = self.input_norm(x)
        lstm_out, _ = self.lstm(x)           # (batch, seq_len, hidden*2)
        context = self.attention(lstm_out)   # (batch, hidden*2)
        out = self.classifier(context)       # (batch, 1)
        return out.squeeze(-1)               # (batch,)


# ======================================================================
# Eğitim & Tahmin Yöneticisi
# ======================================================================

class LSTMTrainer:
    """
    Per-symbol LSTM modeli eğitir ve yönetir.
    Her coin için ayrı model dosyası kaydedilir.
    """

    def __init__(self, exchange, config: dict):
        self.exchange = exchange
        self.cfg = config.get("lstm", {})
        self.fetcher = OHLCVFetcher(exchange, config)

        # Hyperparams
        self.seq_len = self.cfg.get("sequence_length", 60)
        self.hidden_size = self.cfg.get("hidden_size", 128)
        self.num_layers = self.cfg.get("num_layers", 2)
        self.dropout = self.cfg.get("dropout", 0.2)
        self.lr = self.cfg.get("learning_rate", 0.001)
        self.epochs = self.cfg.get("epochs", 50)
        self.batch_size = self.cfg.get("batch_size", 32)
        self.predict_bars = self.cfg.get("predict_bars", 30)
        self.min_accuracy = self.cfg.get("min_accuracy", 0.73)
        self.retrain_hours = self.cfg.get("retrain_hours", 24)

        # In-memory model cache {symbol: {"model": ..., "scaler": ..., "trained_at": ..., "accuracy": ...}}
        self._model_cache: Dict[str, dict] = {}

        # Feature sayısı (data_fetcher.py'deki FEATURE_COLS)
        self.n_features = len(FEATURE_COLS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(self, symbol: str) -> Optional[float]:
        """
        Verilen symbol için fiyat artış olasılığını döndürür.

        Returns:
            float 0.0-1.0 (>0.65 = bullish sinyal), None = model yok/hata
        """
        try:
            model_data = self._get_or_load_model(symbol)
            if model_data is None:
                return None

            model = model_data["model"]
            scaler = model_data["scaler"]

            # Canlı veri çek
            df = self.fetcher.fetch_live_sequence(symbol, self.seq_len + 10)
            if df is None or len(df) < self.seq_len:
                return None

            # Feature matrisini hazırla
            available = [c for c in FEATURE_COLS if c in df.columns]
            seq = df[available].tail(self.seq_len).values.astype(np.float32)

            # NaN/Inf temizle
            seq = np.where(np.isfinite(seq), seq, 0.0)

            # Scaler ile transform
            seq_2d = seq.reshape(-1, seq.shape[-1])
            seq_scaled = scaler.transform(seq_2d).reshape(1, self.seq_len, -1)

            # Tahmin
            model.eval()
            with torch.no_grad():
                x = torch.FloatTensor(seq_scaled).to(DEVICE)
                prob = model(x).item()

            logger.debug(f"LSTM predict {symbol}: {prob:.3f}")
            return prob

        except Exception as e:
            logger.error(f"Predict error for {symbol}: {e}")
            return None

    def train(self, symbol: str, force: bool = False) -> Optional[float]:
        """
        Symbol için LSTM modelini eğitir.

        Args:
            symbol: Örn "BTC/USDT"
            force: True = cache'i yoksay, yeniden eğit

        Returns:
            Val accuracy veya None (eğitim başarısızsa)
        """
        # Yeniden eğitim gerekli mi?
        if not force and self._is_model_fresh(symbol):
            logger.info(f"Model for {symbol} is fresh, skipping retrain")
            return self._model_cache[symbol]["accuracy"]

        logger.info(f"Training LSTM for {symbol}...")

        try:
            # Veri çek
            df = self.fetcher.fetch_training_data(symbol)
            if df is None or len(df) < self.seq_len * 3:
                logger.warning(f"Not enough data to train {symbol}: {len(df) if df is not None else 0}")
                return None

            # Sequence oluştur
            X, y = prepare_sequences(df, self.seq_len, predict_bars=self.predict_bars)
            if len(X) < 100:
                logger.warning(f"Too few sequences for {symbol}: {len(X)}")
                return None

            # Scaler fit (training seti üzerinde)
            n_samples, seq_l, n_feat = X.shape
            X_2d = X.reshape(-1, n_feat)
            scaler = StandardScaler()
            X_scaled_2d = scaler.fit_transform(X_2d)
            X_scaled = X_scaled_2d.reshape(n_samples, seq_l, n_feat)

            # Tensor'a çevir
            X_tensor = torch.FloatTensor(X_scaled)
            y_tensor = torch.FloatTensor(y)

            # Train/val split (%80/%20)
            dataset = TensorDataset(X_tensor, y_tensor)
            val_size = max(int(len(dataset) * 0.2), 50)
            train_size = len(dataset) - val_size
            train_ds, val_ds = random_split(
                dataset,
                [train_size, val_size],
                generator=torch.Generator().manual_seed(42)
            )

            train_loader = DataLoader(train_ds, batch_size=self.batch_size, shuffle=True)
            val_loader = DataLoader(val_ds, batch_size=self.batch_size)

            # Model oluştur
            model = PingerLSTM(
                n_features=n_feat,
                hidden_size=self.hidden_size,
                num_layers=self.num_layers,
                dropout=self.dropout,
            ).to(DEVICE)

            optimizer = optim.Adam(model.parameters(), lr=self.lr, weight_decay=1e-5)
            scheduler = optim.lr_scheduler.ReduceLROnPlateau(
                optimizer, patience=5, factor=0.5, min_lr=1e-6
            )
            criterion = nn.BCELoss()

            # Sınıf dengesizliği için pos_weight
            pos_ratio = float(y.mean())
            if pos_ratio > 0 and pos_ratio < 1:
                pos_weight = torch.tensor([(1 - pos_ratio) / pos_ratio]).to(DEVICE)
                criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
                # Sonunda sigmoid gerekecek (model çıktısı zaten sigmoid içeriyor)
                # Bu yüzden BCELoss kullanmaya devam edelim
                criterion = nn.BCELoss()

            best_val_acc = 0.0
            best_state = None
            patience_counter = 0
            early_stop_patience = 10

            for epoch in range(self.epochs):
                # Eğitim
                model.train()
                train_loss = 0.0
                for xb, yb in train_loader:
                    xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                    optimizer.zero_grad()
                    preds = model(xb)
                    loss = criterion(preds, yb)
                    loss.backward()
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    train_loss += loss.item()

                # Validasyon
                val_acc = self._evaluate(model, val_loader)
                scheduler.step(1 - val_acc)

                if epoch % 10 == 0:
                    logger.info(
                        f"[{symbol}] Epoch {epoch}/{self.epochs} "
                        f"loss={train_loss/len(train_loader):.4f} "
                        f"val_acc={val_acc:.3f}"
                    )

                # Early stopping
                if val_acc > best_val_acc:
                    best_val_acc = val_acc
                    best_state = {k: v.clone() for k, v in model.state_dict().items()}
                    patience_counter = 0
                else:
                    patience_counter += 1
                    if patience_counter >= early_stop_patience:
                        logger.info(f"[{symbol}] Early stop at epoch {epoch}")
                        break

            # En iyi ağırlıkları yükle
            if best_state:
                model.load_state_dict(best_state)

            logger.info(f"[{symbol}] Training complete. Best val_acc: {best_val_acc:.3f}")

            # Cache'e kaydet
            self._model_cache[symbol] = {
                "model": model,
                "scaler": scaler,
                "n_features": n_feat,
                "trained_at": datetime.now(),
                "accuracy": best_val_acc,
            }

            # Diske kaydet
            self._save_model(symbol, model, scaler, n_feat, best_val_acc)

            return best_val_acc

        except Exception as e:
            logger.error(f"Training failed for {symbol}: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Model persistence
    # ------------------------------------------------------------------

    def _save_model(
        self,
        symbol: str,
        model: PingerLSTM,
        scaler: StandardScaler,
        n_features: int,
        accuracy: float,
    ) -> None:
        safe_name = symbol.replace("/", "_")
        path = MODELS_DIR / f"lstm_{safe_name}.pt"
        meta_path = MODELS_DIR / f"lstm_{safe_name}_meta.pkl"

        torch.save(model.state_dict(), path)
        with open(meta_path, "wb") as f:
            pickle.dump({
                "scaler": scaler,
                "n_features": n_features,
                "accuracy": accuracy,
                "hidden_size": self.hidden_size,
                "num_layers": self.num_layers,
                "dropout": self.dropout,
                "seq_len": self.seq_len,
                "saved_at": datetime.now().isoformat(),
            }, f)
        logger.debug(f"Model saved: {path}")

    def _load_model_from_disk(self, symbol: str) -> Optional[dict]:
        safe_name = symbol.replace("/", "_")
        path = MODELS_DIR / f"lstm_{safe_name}.pt"
        meta_path = MODELS_DIR / f"lstm_{safe_name}_meta.pkl"

        if not path.exists() or not meta_path.exists():
            return None

        try:
            with open(meta_path, "rb") as f:
                meta = pickle.load(f)

            model = PingerLSTM(
                n_features=meta["n_features"],
                hidden_size=meta["hidden_size"],
                num_layers=meta["num_layers"],
                dropout=meta["dropout"],
            ).to(DEVICE)
            model.load_state_dict(torch.load(path, map_location=DEVICE))
            model.eval()

            return {
                "model": model,
                "scaler": meta["scaler"],
                "n_features": meta["n_features"],
                "trained_at": datetime.fromisoformat(meta["saved_at"]),
                "accuracy": meta["accuracy"],
            }
        except Exception as e:
            logger.error(f"Failed to load model for {symbol}: {e}")
            return None

    def _get_or_load_model(self, symbol: str) -> Optional[dict]:
        """Cache'ten al, yoksa diskten yükle."""
        if symbol in self._model_cache:
            return self._model_cache[symbol]

        model_data = self._load_model_from_disk(symbol)
        if model_data:
            self._model_cache[symbol] = model_data
            logger.info(
                f"Loaded model for {symbol} "
                f"(acc={model_data['accuracy']:.3f}, "
                f"trained={model_data['trained_at'].strftime('%Y-%m-%d')})"
            )

        return model_data

    def _is_model_fresh(self, symbol: str) -> bool:
        """Model son retrain_hours içinde eğitildiyse True döner."""
        if symbol not in self._model_cache:
            return False
        trained_at = self._model_cache[symbol]["trained_at"]
        age_hours = (datetime.now() - trained_at).total_seconds() / 3600
        return age_hours < self.retrain_hours

    def needs_training(self, symbol: str) -> bool:
        """Modelin eğitilmesi veya yenilenmesi gerekiyor mu?"""
        model_data = self._get_or_load_model(symbol)
        if model_data is None:
            return True
        if not self._is_model_fresh(symbol):
            return True
        if model_data["accuracy"] < self.min_accuracy:
            logger.warning(
                f"{symbol} model accuracy {model_data['accuracy']:.3f} "
                f"< {self.min_accuracy}, will retrain"
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _evaluate(self, model: PingerLSTM, loader: DataLoader) -> float:
        """Validation accuracy hesaplar."""
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for xb, yb in loader:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                preds = model(xb)
                predicted = (preds > 0.5).float()
                correct += (predicted == yb).sum().item()
                total += len(yb)
        return correct / total if total > 0 else 0.0

    def get_model_stats(self, symbol: str) -> dict:
        """Model istatistiklerini döndürür (Telegram bildirimi için)."""
        model_data = self._get_or_load_model(symbol)
        if not model_data:
            return {"trained": False}
        age_h = (datetime.now() - model_data["trained_at"]).total_seconds() / 3600
        return {
            "trained": True,
            "accuracy": model_data["accuracy"],
            "age_hours": round(age_h, 1),
            "trained_at": model_data["trained_at"].strftime("%Y-%m-%d %H:%M"),
        }
