-- ========================================================
-- VIBE-FASHION 쇼핑몰 데이터베이스 스키마
-- ========================================================

-- UUID 확장 활성화 (기본 제공되나 확실히 활성화)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- --------------------------------------------------------
-- 1. profiles (회원 프로필 - auth.users 연동)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    name TEXT,
    phone TEXT,
    avatar_url TEXT,
    role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin')),
    grade TEXT NOT NULL DEFAULT 'BRONZE' CHECK (grade IN ('BRONZE', 'SILVER', 'GOLD', 'VIP')),
    total_spent NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 2. categories (상품 카테고리)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.categories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    parent_id BIGINT REFERENCES public.categories(id) ON DELETE SET NULL,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 3. products (상품)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category_id BIGINT REFERENCES public.categories(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    description TEXT,
    price NUMERIC(12, 2) NOT NULL CHECK (price >= 0),
    sale_price NUMERIC(12, 2) CHECK (sale_price >= 0),
    is_active BOOLEAN NOT NULL DEFAULT true,
    is_featured BOOLEAN NOT NULL DEFAULT false,
    view_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 4. product_options (상품 옵션 - 사이즈, 색상, 재고)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.product_options (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    option_name TEXT NOT NULL, -- 예: '사이즈', '색상'
    option_value TEXT NOT NULL, -- 예: 'L', 'BLACK'
    additional_price NUMERIC(12, 2) NOT NULL DEFAULT 0,
    stock_quantity INT NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 5. product_images (상품 이미지)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.product_images (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    image_url TEXT NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 6. carts (장바구니)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.carts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    option_id BIGINT REFERENCES public.product_options(id) ON DELETE SET NULL,
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT unique_user_product_option UNIQUE (user_id, product_id, option_id)
);

-- --------------------------------------------------------
-- 7. orders (주문)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    order_number TEXT UNIQUE NOT NULL,
    total_amount NUMERIC(12, 2) NOT NULL CHECK (total_amount >= 0),
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    payment_amount NUMERIC(12, 2) NOT NULL CHECK (payment_amount >= 0),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'PAID', 'PREPARING', 'SHIPPED', 'DELIVERED', 'CANCELLED', 'REFUNDED')
    ),
    shipping_address JSONB NOT NULL, -- 배송지 정보 (수령인, 주소, 연락처 등)
    payment_method TEXT,
    payment_key TEXT, -- 결제 PG 연동 식별자
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 8. order_items (주문 상세 항목)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.order_items (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    product_id UUID REFERENCES public.products(id) ON DELETE SET NULL,
    option_id BIGINT REFERENCES public.product_options(id) ON DELETE SET NULL,
    product_name TEXT NOT NULL,
    option_name TEXT,
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    total_price NUMERIC(12, 2) NOT NULL CHECK (total_price >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 9. refunds (환불 / 취소)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.refunds (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    order_item_id BIGINT REFERENCES public.order_items(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL,
    refund_amount NUMERIC(12, 2) NOT NULL CHECK (refund_amount >= 0),
    status TEXT NOT NULL DEFAULT 'REQUESTED' CHECK (
        status IN ('REQUESTED', 'APPROVED', 'REJECTED', 'COMPLETED')
    ),
    admin_memo TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    processed_at TIMESTAMPTZ
);

-- --------------------------------------------------------
-- 10. notifications (알림)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.notifications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    link_url TEXT,
    is_read BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- --------------------------------------------------------
-- 11. reviews (리뷰)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.reviews (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    order_item_id BIGINT UNIQUE REFERENCES public.order_items(id) ON DELETE SET NULL,
    rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    content TEXT,
    images JSONB DEFAULT '[]'::jsonb, -- 리뷰 이미지 URL 배열
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- ========================================================
-- 인덱스 설정 (성능 최적화)
-- ========================================================
CREATE INDEX IF NOT EXISTS idx_products_category_id ON public.products(category_id);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON public.orders(user_id);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON public.order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_reviews_product_id ON public.reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON public.notifications(user_id, is_read);

-- ========================================================
-- 트리거 & 함수 정의
-- ========================================================

-- 1) 소셜 및 이메일 회원가입 시 profiles 자동 생성 트리거
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (
        id,
        email,
        name,
        avatar_url,
        role,
        grade,
        total_spent
    )
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', split_part(NEW.email, '@', 1)),
        COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture'),
        'customer',
        'BRONZE',
        0
    );
    RETURN NEW;
END;
$$;

-- auth.users 트리거 바인딩 (중복 방지 후 재생성)
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- 2) 고객 누적 결제금액 및 등급 자동 업데이트 함수 (update_customer_grade)
-- 등급 기준: 
-- VIP: 1,000,000원 이상
-- GOLD: 500,000원 이상
-- SILVER: 200,000원 이상
-- BRONZE: 200,000원 미만
CREATE OR REPLACE FUNCTION public.update_customer_grade(target_user_id UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_total_spent NUMERIC(12, 2);
    v_grade TEXT;
BEGIN
    -- 결제 완료 또는 배송/완료 상태인 실 결제 금액 합산
    SELECT COALESCE(SUM(payment_amount), 0)
    INTO v_total_spent
    FROM public.orders
    WHERE user_id = target_user_id
      AND status IN ('PAID', 'PREPARING', 'SHIPPED', 'DELIVERED');

    -- 등급 판정
    IF v_total_spent >= 1000000 THEN
        v_grade := 'VIP';
    ELSIF v_total_spent >= 500000 THEN
        v_grade := 'GOLD';
    ELSIF v_total_spent >= 200000 THEN
        v_grade := 'SILVER';
    ELSE
        v_grade := 'BRONZE';
    END IF;

    -- profiles 업데이트
    UPDATE public.profiles
    SET 
        total_spent = v_total_spent,
        grade = v_grade,
        updated_at = timezone('utc'::text, now())
    WHERE id = target_user_id;
END;
$$;

-- 3) 주문 상태 변경 시 update_customer_grade 자동 호출 트리거 함수
CREATE OR REPLACE FUNCTION public.trigger_order_grade_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- 주문자 등급 갱신
    PERFORM public.update_customer_grade(NEW.user_id);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_order_status_change ON public.orders;
CREATE TRIGGER on_order_status_change
    AFTER INSERT OR UPDATE OF status, payment_amount ON public.orders
    FOR EACH ROW
    EXECUTE FUNCTION public.trigger_order_grade_update();

-- ========================================================
-- RLS (Row Level Security) 설정
-- ========================================================
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_options ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.carts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reviews ENABLE ROW LEVEL SECURITY;

-- 공통 기본 조회 정책
-- 카테고리, 상품, 옵션, 이미지는 모든 사용자(익명 포함) 읽기 허용
CREATE POLICY "누구나 카테고리 조회 가능" ON public.categories FOR SELECT USING (true);
CREATE POLICY "누구나 상품 조회 가능" ON public.products FOR SELECT USING (is_active = true);
CREATE POLICY "누구나 상품 옵션 조회 가능" ON public.product_options FOR SELECT USING (true);
CREATE POLICY "누구나 상품 이미지 조회 가능" ON public.product_images FOR SELECT USING (true);
CREATE POLICY "누구나 리뷰 조회 가능" ON public.reviews FOR SELECT USING (true);

-- 사용자 개인 데이터 정책 (본인 데이터만 CRUD)
CREATE POLICY "본인 프로필 조회" ON public.profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "본인 프로필 수정" ON public.profiles FOR UPDATE USING (auth.uid() = id);

CREATE POLICY "본인 장바구니 관리" ON public.carts FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "본인 주문 내역 조회" ON public.orders FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "본인 주문 생성" ON public.orders FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "본인 알림 조회 및 수정" ON public.notifications FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "본인 환불 신청 및 조회" ON public.refunds FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "본인 리뷰 작성 및 관리" ON public.reviews FOR ALL USING (auth.uid() = user_id);
